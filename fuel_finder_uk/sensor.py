"""Sensor platform for Fuel Finder UK integration."""
import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

FUEL_TYPES = {
    "E10": "Petrol E10",
    "E5": "Petrol E5",
    "B7": "Diesel",
    "SDV": "Super Diesel",
}

# Petrol types get the pump icon, diesel types get the outline variant
FUEL_ICONS = {
    "E10": "mdi:gas-station",
    "E5": "mdi:gas-station",
    "B7": "mdi:gas-station-outline",
    "SDV": "mdi:gas-station-outline",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform."""
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []

    # Add cheapest price sensor for each fuel type
    for fuel_code, fuel_name in FUEL_TYPES.items():
        entities.append(
            FuelFinderCheapestPriceSensor(
                coordinator,
                entry,
                fuel_code,
                fuel_name,
            )
        )

    # Add additional sensors
    entities.extend(
        [
            FuelFinderStationCountSensor(coordinator, entry),
            FuelFinderUpdateTimeSensor(coordinator, entry),
        ]
    )

    async_add_entities(entities)


class FuelFinderBaseSensor(CoordinatorEntity, SensorEntity):
    """Base sensor for Fuel Finder."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entry = entry
        self._attr_has_entity_name = True
        location_name = entry.data.get("location_name")
        device_label = (
            f"UK Fuel Finder ({location_name})"
            if location_name
            else f"UK Fuel Finder ({entry.data.get('latitude')}, {entry.data.get('longitude')})"
        )

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=device_label,
            manufacturer="UK Fuel Finder",
            model="Fuel Finder API",
            entry_type=DeviceEntryType.SERVICE,
        )


class FuelFinderCheapestPriceSensor(FuelFinderBaseSensor):
    """Sensor for cheapest fuel price."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
        fuel_type: str,
        fuel_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self.fuel_type = fuel_type
        self.fuel_name = fuel_name
        
        self._attr_unique_id = f"{entry.entry_id}-cheapest-{fuel_type}"
        self._attr_name = f"Cheapest {fuel_name} Price"
        self._attr_native_unit_of_measurement = "p/L"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = FUEL_ICONS.get(fuel_type, "mdi:gas-station")

    @property
    def native_value(self) -> float | None:
        """Return the cheapest price."""
        if not self.coordinator.data:
            return None

        cheapest = self.coordinator.get_cheapest_price(self.fuel_type)
        
        if cheapest:
            return cheapest.get("price")
        
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        if not self.coordinator.data:
            return {}

        cheapest = self.coordinator.get_cheapest_price(self.fuel_type)
        
        if not cheapest:
            return {}

        station = cheapest.get("station", {})
        location = station.get("location") or {}
        
        return {
            "station_name": station.get("trading_name") or station.get("name"),
            "brand": station.get("brand_name"),
            "postcode": location.get("postcode"),
            "station_address": ", ".join(
                filter(
                    None,
                    [
                        location.get("address_line_1"),
                        location.get("address_line_2"),
                        location.get("city"),
                        location.get("postcode"),
                    ],
                )
            ) or station.get("address"),
            "latitude": location.get("latitude") or station.get("lat"),
            "longitude": location.get("longitude") or station.get("lon"),
            "last_updated": cheapest.get("last_updated"),
            "distance": station.get("distance"),
            "distance_driving": station.get("distance_driving"),
        }


class FuelFinderStationCountSensor(FuelFinderBaseSensor):
    """Sensor for number of nearby stations."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        
        self._attr_unique_id = f"{entry.entry_id}-station-count"
        self._attr_name = "Nearby Stations"
        self._attr_icon = "mdi:gas-station"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "stations"

    @property
    def native_value(self) -> int | None:
        """Return the station count."""
        if not self.coordinator.data:
            return None

        stations = self.coordinator.data.get("stations", [])
        return len(stations)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the list of nearby stations."""
        if not self.coordinator.data:
            return {}

        stations = self.coordinator.data.get("stations", [])

        station_list = []
        for station in stations:
            if not isinstance(station, dict):
                continue

            location = station.get("location") or {}
            station_list.append(
                {
                    "name": station.get("trading_name") or station.get("name"),
                    "brand": station.get("brand_name"),
                    "distance_miles": station.get("distance"),
                    "distance_driving_miles": station.get("distance_driving"),
                    "postcode": location.get("postcode"),
                    "fuel_types": station.get("fuel_types"),
                }
            )

        def sort_key(s: dict[str, Any]) -> float:
            # Prefer driving distance once it's been fetched/cached; fall
            # back to straight-line distance for stations that don't have
            # a cached driving distance yet (e.g. no ORS key configured,
            # or the station was only just discovered this refresh).
            driving = s.get("distance_driving_miles")
            if driving is not None:
                return driving
            straight_line = s.get("distance_miles")
            return straight_line if straight_line is not None else float("inf")

        # Nearest-first so the list is useful at a glance
        station_list.sort(key=sort_key)

        return {"stations": station_list}


class FuelFinderUpdateTimeSensor(FuelFinderBaseSensor):
    """Sensor for last update time."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        
        self._attr_unique_id = f"{entry.entry_id}-last-update"
        self._attr_name = "Last Update"
        self._attr_icon = "mdi:clock"

    @property
    def native_value(self) -> str | None:
        """Return the last update time."""
        if self.coordinator.last_successful_update:
            return self.coordinator.last_successful_update.isoformat()
        
        return None
