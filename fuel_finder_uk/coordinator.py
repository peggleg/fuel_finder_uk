"""Data coordinator for Fuel Finder integration."""
import asyncio
import logging
from datetime import timedelta, datetime
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

API_BASE = "https://www.fuel-finder.service.gov.uk"
TOKEN_URL = f"{API_BASE}/api/v1/oauth/generate_access_token"
STATIONS_URL = f"{API_BASE}/api/v1/pfs"
PRICES_URL = f"{API_BASE}/api/v1/pfs/fuel-prices"

ORS_MATRIX_URL = "https://api.heigit.org/openrouteservice/v2/matrix/driving-car"
# Cached driving distances are considered fresh for this long; road distance
# between two fixed points doesn't change day to day, so there's no need to
# re-fetch it on every price refresh.
DRIVING_DISTANCE_CACHE_TTL = timedelta(hours=24)

# Maps the sensor-facing fuel type codes used by this integration to the
# actual API fuel_type codes seen in station/price data. The API appears to
# encode diesel grades as B7_STANDARD (regular diesel) and B7_PREMIUM
# (super/premium diesel), rather than a distinct "SDV" code, so SDV is
# mapped to B7_PREMIUM. Multiple candidate codes are listed per type in
# case the API uses different codes in different regions/stations.
FUEL_TYPE_ALIASES: dict[str, list[str]] = {
    "E10": ["E10"],
    "E5": ["E5"],
    "B7": ["B7_STANDARD", "B7"],
    "SDV": ["B7_PREMIUM", "SDV", "SDV_STANDARD", "SUPER_DIESEL"],
}


class FuelFinderDataCoordinator(DataUpdateCoordinator):
    """Data coordinator for Fuel Finder API."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        update_interval_minutes = entry.data.get("update_interval", 15)
        update_interval = timedelta(minutes=update_interval_minutes)
        
        super().__init__(
            hass,
            _LOGGER,
            name="Fuel Finder UK",
            update_interval=update_interval,
        )
        self.entry = entry
        self.client_id = entry.data.get("client_id")
        self.client_secret = entry.data.get("client_secret")
        self.latitude = entry.data.get("latitude")
        self.longitude = entry.data.get("longitude")
        self.radius = entry.data.get("radius", 5)  # miles
        self.access_token: str | None = None
        self.token_expires_at: float = 0
        self.last_successful_update: datetime | None = None
        self.ors_api_key = entry.data.get("ors_api_key") or None
        # Cache of driving distances keyed by station node_id:
        # {node_id: {"distance_driving": float, "cached_at": datetime}}
        self._driving_distance_cache: dict[str, dict[str, Any]] = {}
        
    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API."""
        try:
            async with aiohttp.ClientSession() as session:
                # Get or refresh access token
                await self._async_get_access_token(session)
                
                # Fetch all stations and prices
                data = await self._async_get_all_data(session)
                
                return data
                
        except asyncio.TimeoutError as err:
            raise UpdateFailed(f"Timeout connecting to Fuel Finder API: {err}") from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Error connecting to Fuel Finder API: {err}") from err
        except UpdateFailed:
            # Re-raise UpdateFailed exceptions as-is
            raise
        except Exception as err:
            import traceback
            tb = traceback.format_exc()
            _LOGGER.error(f"Unexpected error in _async_update_data: {err}\n{tb}")
            raise UpdateFailed(f"Unexpected error: {err}") from err

    async def _async_get_access_token(self, session: aiohttp.ClientSession) -> str:
        """Get OAuth access token."""
        import time
        import asyncio
        
        current_time = time.time()
        
        # Check if token is still valid
        if self.access_token and current_time < self.token_expires_at - 300:  # 5 min buffer
            return self.access_token
        
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        
        # Retry logic for rate limiting
        max_retries = 3
        for attempt in range(max_retries):
            try:
                _LOGGER.debug(f"Token request attempt {attempt + 1}/{max_retries}")
                async with session.post(TOKEN_URL, json=data, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        
                        # Handle rate limiting with backoff
                        if resp.status == 429 and attempt < max_retries - 1:
                            wait_time = (2 ** attempt)  # 1s, 2s, 4s
                            _LOGGER.debug(f"Token request rate limited (429), retrying in {wait_time}s...")
                            await asyncio.sleep(wait_time)
                            continue
                        
                        raise UpdateFailed(f"Token request failed: {resp.status} - {error_text}")
                    
                    token_data = await resp.json()
                    _LOGGER.debug(f"Token response keys: {list(token_data.keys()) if isinstance(token_data, dict) else 'not a dict'}")
                    
                    # The API wraps the token in a "data" key
                    if isinstance(token_data, dict) and "data" in token_data:
                        token_data = token_data["data"]
                    
                    self.access_token = token_data.get("access_token")
                    if not self.access_token:
                        raise UpdateFailed(f"No access_token in response: {token_data}")
                    
                    expires_in = token_data.get("expires_in", 3600)
                    self.token_expires_at = current_time + expires_in
                    
                    _LOGGER.debug(f"Successfully obtained access token (expires in {expires_in}s)")
                    return self.access_token
                    
            except asyncio.TimeoutError:
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt)
                    _LOGGER.debug(f"Token request timeout, retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    continue
                raise UpdateFailed(f"Token request timeout after {max_retries} attempts")
            except Exception as err:
                import traceback
                _LOGGER.error(f"Token request error (attempt {attempt + 1}): {err}\n{traceback.format_exc()}")
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt)
                    await asyncio.sleep(wait_time)
                    continue
                raise UpdateFailed(f"Failed to get access token: {err}") from err
                
        raise UpdateFailed("Failed to get access token after retries")

    async def _async_get_all_data(self, session: aiohttp.ClientSession) -> dict[str, Any]:
        """Get all stations and prices from API."""
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }
        
        _LOGGER.debug(f"Making API request with Authorization header: Bearer {self.access_token[:20] if self.access_token else 'None'}...")
        
        try:
            stations_data = None
            prices_data = None
            all_stations = []
            all_prices = []
            
            # Fetch all stations (paginated with batch-number)
            try:
                batch_number = 1
                while True:
                    _LOGGER.debug(f"Fetching stations batch {batch_number}")
                    params = {"batch-number": batch_number}
                    
                    async with session.get(
                        STATIONS_URL,
                        headers=headers,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        if resp.status != 200:
                            error_text = await resp.text()
                            # 404 means no more batches - this is expected
                            if resp.status == 404:
                                _LOGGER.debug(f"No more station batches (got 404 on batch {batch_number})")
                                break
                            _LOGGER.error(f"Failed to get stations batch {batch_number}: {resp.status} - {error_text}")
                            # On first batch error, raise; otherwise continue with what we have
                            if batch_number == 1:
                                raise UpdateFailed(f"Failed to fetch stations: {resp.status} - {error_text}")
                            break
                        
                        batch_data = await resp.json()
                        _LOGGER.debug(f"Stations batch {batch_number} response type: {type(batch_data)}")
                        
                        # Extract station data
                        if isinstance(batch_data, dict):
                            # Unwrap nested response if needed
                            if "data" in batch_data:
                                batch_data = batch_data["data"]
                            
                            if isinstance(batch_data, list):
                                all_stations.extend(batch_data)
                            elif isinstance(batch_data, dict):
                                if "data" in batch_data:
                                    stations = batch_data["data"]
                                    if isinstance(stations, list):
                                        all_stations.extend(stations)
                                else:
                                    # Single station or empty
                                    if any(k in batch_data for k in ["lat", "lon", "uuid"]):
                                        all_stations.append(batch_data)
                        elif isinstance(batch_data, list):
                            all_stations.extend(batch_data)
                        
                        # Check if there are more batches
                        batch_number += 1
                        if batch_number > 100:  # Safety limit
                            _LOGGER.warning("Hit safety limit of 100 batches")
                            break
                        
            except UpdateFailed:
                raise
            except Exception as err:
                import traceback
                _LOGGER.error(f"Error fetching stations: {err}\n{traceback.format_exc()}")
                raise UpdateFailed(f"Error fetching stations: {err}") from err
            
            _LOGGER.debug(f"Fetched {len(all_stations)} total stations")
            stations_data = {"data": all_stations}
            
            # Fetch all prices (paginated with batch-number)
            try:
                batch_number = 1
                while True:
                    _LOGGER.debug(f"Fetching prices batch {batch_number}")
                    params = {"batch-number": batch_number}
                    
                    async with session.get(
                        PRICES_URL,
                        headers=headers,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        if resp.status != 200:
                            error_text = await resp.text()
                            # 404 means no more batches - this is expected
                            if resp.status == 404:
                                _LOGGER.debug(f"No more price batches (got 404 on batch {batch_number})")
                                break
                            _LOGGER.error(f"Failed to get prices batch {batch_number}: {resp.status} - {error_text}")
                            # On first batch error, raise; otherwise continue with what we have
                            if batch_number == 1:
                                raise UpdateFailed(f"Failed to fetch prices: {resp.status} - {error_text}")
                            break
                        
                        batch_data = await resp.json()
                        _LOGGER.debug(f"Prices batch {batch_number} response type: {type(batch_data)}")
                        
                        # Extract price data
                        if isinstance(batch_data, dict):
                            # Unwrap nested response if needed
                            if "data" in batch_data:
                                batch_data = batch_data["data"]
                            
                            if isinstance(batch_data, list):
                                all_prices.extend(batch_data)
                            elif isinstance(batch_data, dict):
                                if "data" in batch_data:
                                    prices = batch_data["data"]
                                    if isinstance(prices, list):
                                        all_prices.extend(prices)
                                else:
                                    # Single price or empty
                                    if any(k in batch_data for k in ["pfs_id", "prices", "fuel_type"]):
                                        all_prices.append(batch_data)
                        elif isinstance(batch_data, list):
                            all_prices.extend(batch_data)
                        
                        # Check if there are more batches
                        batch_number += 1
                        if batch_number > 100:  # Safety limit
                            _LOGGER.warning("Hit safety limit of 100 batches for prices")
                            break
                        
            except UpdateFailed:
                raise
            except Exception as err:
                import traceback
                _LOGGER.error(f"Error fetching prices: {err}\n{traceback.format_exc()}")
                raise UpdateFailed(f"Error fetching prices: {err}") from err
            
            _LOGGER.debug(f"Fetched {len(all_prices)} total prices")
            prices_data = {"data": all_prices}
            
            # Filter stations by radius and location
            try:
                filtered_stations = self._filter_nearby_stations(stations_data)
            except Exception as err:
                import traceback
                _LOGGER.error(f"Error filtering stations: {err}\n{traceback.format_exc()}")
                raise UpdateFailed(f"Error filtering stations: {err}") from err
            
            # Enrich with driving distance if an ORS API key is configured.
            # This never raises - a failure here just means stations keep
            # whatever driving distance they had cached (or none at all),
            # falling back to straight-line distance downstream.
            if self.ors_api_key:
                try:
                    await self._async_enrich_driving_distances(session, filtered_stations)
                except Exception as err:
                    _LOGGER.debug(f"Driving distance enrichment failed, continuing with straight-line only: {err}")
            
            # Match prices to stations
            try:
                prices_by_station = self._match_prices_to_stations(filtered_stations, prices_data)
            except Exception as err:
                import traceback
                _LOGGER.error(f"Error matching prices: {err}\n{traceback.format_exc()}")
                raise UpdateFailed(f"Error matching prices: {err}") from err
            
            self.last_successful_update = dt_util.utcnow()
            
            return {
                "stations": filtered_stations,
                "prices": prices_by_station,
                "location": {
                    "latitude": self.latitude,
                    "longitude": self.longitude,
                    "radius": self.radius,
                }
            }
                
        except UpdateFailed:
            raise
        except Exception as err:
            import traceback
            _LOGGER.error(f"Unexpected error in _async_get_all_data: {err}\n{traceback.format_exc()}")
            raise UpdateFailed(f"Unexpected error in data fetch: {err}") from err

    def _filter_nearby_stations(self, stations_data: dict) -> list[dict]:
        """Filter stations within radius."""
        from math import radians, cos, sin, asin, sqrt
        
        def haversine(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
            """Calculate distance between two points in miles."""
            lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
            dlon = lon2 - lon1
            dlat = lat2 - lat1
            a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
            c = 2 * asin(sqrt(a))
            miles = 3959 * c  # Earth's radius in miles
            return miles
        
        # Handle different possible response structures
        if not stations_data:
            _LOGGER.warning("No stations data received")
            return []
        
        # Try to get the data - it could be a dict with "data" key or a list directly
        if isinstance(stations_data, dict):
            stations = stations_data.get("data", [])
            if not stations:
                # Try other possible keys
                stations = stations_data.get("stations", [])
                if not stations:
                    # Try the raw dict itself if it looks like station data
                    if "lat" in stations_data and "lon" in stations_data:
                        stations = [stations_data]
        elif isinstance(stations_data, list):
            stations = stations_data
        else:
            _LOGGER.error(f"Unexpected stations data type: {type(stations_data)}")
            return []
        
        # Log a sample record so we can see the real field names/shape from the API
        if stations and isinstance(stations[0], dict):
            _LOGGER.debug(f"Sample station record keys: {list(stations[0].keys())}")
        
        filtered = []
        
        for station in stations:
            try:
                if not isinstance(station, dict):
                    continue
                
                # Coordinates are nested under "location" in the real API schema,
                # but fall back to top-level/alternate keys defensively.
                location = station.get("location") or {}
                
                station_lat = (
                    location.get("latitude")
                    or station.get("lat")
                    or station.get("latitude")
                )
                station_lon = (
                    location.get("longitude")
                    or station.get("lon")
                    or station.get("lng")
                    or station.get("longitude")
                )
                
                if station_lat is None or station_lon is None:
                    continue
                
                try:
                    station_lat = float(station_lat)
                    station_lon = float(station_lon)
                except (ValueError, TypeError):
                    continue
                    
                distance = haversine(self.longitude, self.latitude, station_lon, station_lat)
                
                if distance <= self.radius:
                    station["distance"] = round(distance, 1)
                    filtered.append(station)
            except Exception as err:
                _LOGGER.debug(f"Error processing station: {err}")
                continue
        
        _LOGGER.debug(f"Found {len(filtered)} stations within {self.radius}mi from {len(stations)} total")
        return filtered

    async def _async_enrich_driving_distances(
        self,
        session: aiohttp.ClientSession,
        stations: list[dict],
    ) -> None:
        """Add a 'distance_driving' field (miles) to each station in place.

        Uses the ORS Matrix API with a single request covering every
        station that doesn't already have a fresh cached value. Results
        are cached per station node_id for DRIVING_DISTANCE_CACHE_TTL,
        since road distance between two fixed points doesn't change day
        to day - this keeps API usage far below the free tier limit even
        though price data refreshes much more often.
        """
        now = dt_util.utcnow()

        # Apply anything still-fresh from cache first, and collect the
        # stations that actually need a fresh lookup.
        stations_needing_lookup = []
        for station in stations:
            node_id = station.get("node_id") or station.get("uuid") or station.get("id")
            if not node_id:
                continue

            cached = self._driving_distance_cache.get(node_id)
            if cached and (now - cached["cached_at"]) < DRIVING_DISTANCE_CACHE_TTL:
                station["distance_driving"] = cached["distance_driving"]
            else:
                stations_needing_lookup.append(station)

        if not stations_needing_lookup:
            return

        # ORS Matrix expects [longitude, latitude] pairs, origin first.
        locations = [[self.longitude, self.latitude]]
        for station in stations_needing_lookup:
            location = station.get("location") or {}
            lat = location.get("latitude") or station.get("lat")
            lon = location.get("longitude") or station.get("lon")
            if lat is None or lon is None:
                continue
            locations.append([float(lon), float(lat)])

        if len(locations) < 2:
            return

        body = {
            "locations": locations,
            "sources": [0],
            "destinations": list(range(1, len(locations))),
            "metrics": ["distance"],
            "units": "mi",
        }
        headers = {
            "Authorization": self.ors_api_key,
            "Content-Type": "application/json",
        }

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with session.post(
                    ORS_MATRIX_URL,
                    json=body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 429 and attempt < max_retries - 1:
                        wait_time = 2 ** attempt
                        _LOGGER.debug(f"ORS Matrix rate limited (429), retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue

                    if resp.status != 200:
                        error_text = await resp.text()
                        _LOGGER.debug(f"ORS Matrix request failed: {resp.status} - {error_text}")
                        return

                    result = await resp.json()
                    break
            except asyncio.TimeoutError:
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                _LOGGER.debug("ORS Matrix request timed out after retries")
                return
        else:
            return

        distances = (result or {}).get("distances")
        if not distances or not isinstance(distances, list) or not distances[0]:
            _LOGGER.debug(f"Unexpected ORS Matrix response shape: {result}")
            return

        # distances[0] is the one row for our single origin, one value per destination
        row = distances[0]
        for station, driving_distance in zip(stations_needing_lookup, row):
            if driving_distance is None:
                continue
            node_id = station.get("node_id") or station.get("uuid") or station.get("id")
            value = round(float(driving_distance), 1)
            station["distance_driving"] = value
            if node_id:
                self._driving_distance_cache[node_id] = {
                    "distance_driving": value,
                    "cached_at": now,
                }

        _LOGGER.debug(f"Fetched driving distance for {len(row)} stations via ORS Matrix")

    def _match_prices_to_stations(
        self, 
        stations: list[dict], 
        prices_data: dict
    ) -> dict[str, Any]:
        """Match price data to stations, restricted to the given (nearby) stations."""
        prices_by_station = {}
        
        if not prices_data:
            _LOGGER.warning("No prices data received")
            return prices_by_station
        
        # Build the set of station IDs we actually care about (already radius-filtered)
        nearby_ids = set()
        for s in stations:
            if isinstance(s, dict):
                station_id = (
                    s.get("node_id")
                    or s.get("uuid")
                    or s.get("id")
                    or s.get("pfs_id")
                    or s.get("site_id")
                )
                if station_id:
                    nearby_ids.add(station_id)
        
        # Handle different possible response structures
        if isinstance(prices_data, dict):
            prices = prices_data.get("data", [])
            if not prices:
                # Try other possible keys
                prices = prices_data.get("prices", [])
        elif isinstance(prices_data, list):
            prices = prices_data
        else:
            _LOGGER.error(f"Unexpected prices data type: {type(prices_data)}")
            return prices_by_station
        
        # Log a sample record so we can see the real field names/shape from the API
        if prices and isinstance(prices[0], dict):
            _LOGGER.debug(f"Sample price record keys: {list(prices[0].keys())}")
        
        for price in prices:
            try:
                if not isinstance(price, dict):
                    continue
                    
                station_uuid = (
                    price.get("pfs_id")
                    or price.get("uuid")
                    or price.get("id")
                    or price.get("node_id")
                    or price.get("site_id")
                )
                # Only keep prices for stations that are actually within radius
                if station_uuid and station_uuid in nearby_ids:
                    prices_by_station[station_uuid] = price
            except Exception as err:
                _LOGGER.debug(f"Error processing price: {err}")
                continue
        
        _LOGGER.debug(f"Matched {len(prices_by_station)} prices to {len(nearby_ids)} nearby stations")
        return prices_by_station

    def get_cheapest_price(self, fuel_type: str) -> dict[str, Any] | None:
        """Get cheapest station for specific fuel type."""
        if not self.data:
            return None
        
        try:
            prices_by_station = self.data.get("prices", {})
            if not prices_by_station:
                return None
            
            stations = {}
            for s in self.data.get("stations", []):
                if isinstance(s, dict):
                    # Try multiple possible ID fields
                    station_id = (
                        s.get("node_id")
                        or s.get("uuid")
                        or s.get("id")
                        or s.get("pfs_id")
                        or s.get("site_id")
                    )
                    if station_id:
                        stations[station_id] = s
            
            cheapest = None
            cheapest_price = float("inf")
            
            # Accept any of the known API codes for this sensor's fuel type
            accepted_codes = set(FUEL_TYPE_ALIASES.get(fuel_type, [fuel_type]))
            
            seen_codes: set[str] = set()
            
            for station_uuid, price_data in prices_by_station.items():
                if not isinstance(price_data, dict):
                    continue
                
                # The real API nests the price list under "fuel_prices";
                # fall back to other possible shapes defensively.
                prices = (
                    price_data.get("fuel_prices")
                    or price_data.get("prices")
                    or []
                )
                if not prices and "fuel_type" in price_data:
                    # Single price object, not a list
                    prices = [price_data]
                
                for price in prices:
                    if not isinstance(price, dict):
                        continue
                    
                    raw_fuel_type = price.get("fuel_type", "")
                    if raw_fuel_type:
                        seen_codes.add(raw_fuel_type)
                    
                    if raw_fuel_type not in accepted_codes:
                        continue
                    
                    try:
                        price_value = float(price.get("price", 0))
                        if price_value > 0 and price_value < cheapest_price:
                            cheapest_price = price_value
                            station = stations.get(station_uuid, {})
                            cheapest = {
                                "station": station,
                                "price": price_value,
                                "last_updated": price.get("price_last_updated") or price.get("last_updated"),
                            }
                    except (ValueError, TypeError):
                        continue
            
            if not cheapest:
                _LOGGER.debug(
                    f"No price found for '{fuel_type}' (accepted codes: {accepted_codes}). "
                    f"Fuel type codes seen among nearby stations: {sorted(seen_codes)}"
                )
            
            return cheapest
        except Exception as err:
            _LOGGER.error(f"Error getting cheapest price: {err}")
            return None

