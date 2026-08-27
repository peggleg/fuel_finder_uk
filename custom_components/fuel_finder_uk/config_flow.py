"""Config flow for Fuel Finder UK integration."""
import logging
import uuid
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("location_name"): cv.string,
        vol.Required("client_id"): cv.string,
        vol.Required("client_secret"): cv.string,
        vol.Required("latitude"): cv.latitude,
        vol.Required("longitude"): cv.longitude,
        vol.Optional("radius", default=5): cv.positive_int,
        vol.Optional("update_interval", default=15): cv.positive_int,
        vol.Optional("ors_api_key"): cv.string,
    }
)


class FuelFinderConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Fuel Finder UK."""

    VERSION = 1

    def _is_duplicate_location(
        self, latitude: float, longitude: float, exclude_entry_id: str | None = None
    ) -> bool:
        """Check whether another entry already tracks this exact location.

        unique_id is no longer derived from coordinates (it's a permanent
        random ID assigned at creation), so duplicate-location prevention
        has to be done explicitly instead of relying on unique_id matching.
        """
        for entry in self._async_current_entries():
            if entry.entry_id == exclude_entry_id:
                continue
            if entry.data.get("latitude") == latitude and entry.data.get("longitude") == longitude:
                return True
        return False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate the credentials
            valid = await self._validate_credentials(
                user_input["client_id"],
                user_input["client_secret"],
            )

            if not valid:
                errors["base"] = "invalid_auth"
            elif self._is_duplicate_location(user_input["latitude"], user_input["longitude"]):
                errors["base"] = "already_configured"
            else:
                # unique_id is a permanent random ID, independent of the
                # coordinates, so that location can be edited later via
                # reconfigure without ever needing to change unique_id
                # (which Home Assistant expects to stay constant for the
                # life of the entry).
                await self.async_set_unique_id(str(uuid.uuid4()))

                # Freeze the search radius used for the favourite-station
                # picker at whatever radius was chosen right now. This is
                # deliberately separate from "radius" (which stays live and
                # editable via reconfigure) so narrowing/widening the price
                # search radius later never affects which stations remain
                # pickable as a favourite.
                entry_data = {
                    **user_input,
                    "favourite_search_radius": user_input["radius"],
                }

                return self.async_create_entry(
                    title=f"UK Fuel Finder - {user_input['location_name']}",
                    data=entry_data,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "setup_link": "https://www.developer.fuel-finder.service.gov.uk",
                "update_info": "(5-1440 minutes; default 15 matches API refresh rate)"
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Allow editing an existing entry's settings, including its location."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()
        current = reconfigure_entry.data

        if user_input is not None:
            valid = await self._validate_credentials(
                user_input["client_id"],
                user_input["client_secret"],
            )

            if not valid:
                errors["base"] = "invalid_auth"
            elif self._is_duplicate_location(
                user_input["latitude"],
                user_input["longitude"],
                exclude_entry_id=reconfigure_entry.entry_id,
            ):
                errors["base"] = "already_configured"
            else:
                # Merge rather than replace entry.data outright - this form
                # doesn't include every field (e.g. favourite_search_radius
                # is deliberately frozen and never editable here), and a
                # full replace would silently wipe anything not in this
                # specific schema.
                self.hass.config_entries.async_update_entry(
                    reconfigure_entry,
                    data={**reconfigure_entry.data, **user_input},
                    title=f"UK Fuel Finder - {user_input['location_name']}",
                )
                # No explicit reload here - the update listener registered in
                # __init__.py already reloads the entry automatically whenever
                # its data changes. Calling async_reload() again here caused
                # two overlapping reloads racing each other.
                return self.async_abort(reason="reconfigure_successful")

        reconfigure_schema = vol.Schema(
            {
                vol.Required(
                    "location_name", default=current.get("location_name", "")
                ): cv.string,
                vol.Required("client_id", default=current.get("client_id", "")): cv.string,
                vol.Required(
                    "client_secret", default=current.get("client_secret", "")
                ): cv.string,
                vol.Required(
                    "latitude", default=current.get("latitude")
                ): cv.latitude,
                vol.Required(
                    "longitude", default=current.get("longitude")
                ): cv.longitude,
                vol.Optional("radius", default=current.get("radius", 5)): cv.positive_int,
                vol.Optional(
                    "update_interval", default=current.get("update_interval", 15)
                ): cv.positive_int,
                vol.Optional(
                    "ors_api_key", default=current.get("ors_api_key", "")
                ): cv.string,
            }
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=reconfigure_schema,
            errors=errors,
            description_placeholders={
                "location_name": current.get("location_name", "this location"),
            },
        )

    async def _validate_credentials(self, client_id: str, client_secret: str) -> bool:
        """Validate OAuth credentials."""
        import aiohttp
        import asyncio
        
        token_url = "https://www.fuel-finder.service.gov.uk/api/v1/oauth/generate_access_token"
        
        try:
            async with aiohttp.ClientSession() as session:
                data = {
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                }
                
                # Retry logic for rate limiting
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        async with session.post(
                            token_url,
                            json=data,
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as resp:
                            if resp.status == 200:
                                _LOGGER.debug("OAuth credentials validated successfully")
                                return True
                            elif resp.status == 429:
                                # Rate limited - retry with exponential backoff
                                if attempt < max_retries - 1:
                                    wait_time = (2 ** attempt)  # 1s, 2s, 4s
                                    _LOGGER.debug(f"Rate limited (429), retrying in {wait_time}s...")
                                    await asyncio.sleep(wait_time)
                                    continue
                                else:
                                    error_text = await resp.text()
                                    _LOGGER.error(f"OAuth validation rate limited (429): {error_text}")
                                    return False
                            else:
                                error_text = await resp.text()
                                _LOGGER.error(f"OAuth validation failed: {resp.status} - {error_text}")
                                return False
                    except asyncio.TimeoutError:
                        if attempt < max_retries - 1:
                            wait_time = (2 ** attempt)
                            _LOGGER.debug(f"Timeout, retrying in {wait_time}s...")
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            raise
                
        except Exception as err:
            _LOGGER.error(f"Error validating credentials: {err}")
            return False
