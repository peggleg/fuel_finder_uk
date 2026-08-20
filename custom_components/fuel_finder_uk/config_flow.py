"""Config flow for Fuel Finder UK integration."""
import logging
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
            else:
                await self.async_set_unique_id(
                    f"{user_input['latitude']}-{user_input['longitude']}"
                )
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"UK Fuel Finder - {user_input['location_name']}",
                    data=user_input,
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
