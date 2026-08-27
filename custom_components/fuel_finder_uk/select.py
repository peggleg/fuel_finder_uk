"""Select platform for Fuel Finder UK integration - favourite station picker."""
import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

NONE_OPTION = "None"


def _station_node_id(station: dict) -> str | None:
    """Extract a station's identifier, trying every field name seen in the API."""
    return (
        station.get("node_id")
        or station.get("uuid")
        or station.get("id")
        or station.get("pfs_id")
        or station.get("site_id")
    )


def _candidate_label(station: dict) -> str | None:
    """Build the 'Name, Postcode' label shown in the dropdown for one station."""
    name = station.get("trading_name") or station.get("name")
    if not name:
        return None
    location = station.get("location") or {}
    postcode = location.get("postcode")
    return f"{name}, {postcode}" if postcode else name


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select platform."""
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([FuelFinderFavouriteSelect(coordinator, entry)])


class FuelFinderFavouriteSelect(CoordinatorEntity, RestoreEntity, SelectEntity):
    """Lets the user pick a favourite station to compare against the cheapest price.

    Picking a favourite here is deliberately lightweight - it updates the
    coordinator directly and reuses already-fetched data (plus, if needed,
    a single small driving-distance lookup), rather than triggering a full
    config entry reload or a fresh government API fetch.
    """

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self.entry = entry
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{entry.entry_id}-favourite-station"
        self._attr_name = "Favourite Station"
        self._attr_icon = "mdi:star"

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

    async def async_added_to_hass(self) -> None:
        """Restore the previously-selected favourite, if any, on startup."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is not None:
            restored_node_id = last_state.attributes.get("node_id")
            if restored_node_id:
                await self.coordinator.async_set_favourite(restored_node_id)

    def _candidates(self) -> list[dict]:
        """Return the current list of stations pickable as a favourite."""
        if not self.coordinator.data:
            return []
        return self.coordinator.data.get("favourite_candidates", [])

    @property
    def options(self) -> list[str]:
        """Return the list of selectable options."""
        labels = [NONE_OPTION]
        for station in self._candidates():
            label = _candidate_label(station)
            if label:
                labels.append(label)
        return labels

    @property
    def current_option(self) -> str | None:
        """Return the currently selected favourite, as its display label."""
        node_id = self.coordinator.favourite_node_id
        if not node_id:
            return NONE_OPTION

        for station in self._candidates():
            if _station_node_id(station) == node_id:
                return _candidate_label(station) or NONE_OPTION

        # Favourite is set but not currently among the pickable candidates
        # (e.g. it's since disappeared from the API entirely). Fall back to
        # whatever the coordinator directly resolved, so the label doesn't
        # just vanish from the dashboard.
        favourite_station = (self.coordinator.data or {}).get("favourite_station")
        if favourite_station:
            label = _candidate_label(favourite_station)
            if label:
                return label

        return NONE_OPTION

    async def async_select_option(self, option: str) -> None:
        """Handle the user picking a new favourite from the dropdown."""
        if option == NONE_OPTION:
            await self.coordinator.async_set_favourite(None)
            self.async_write_ha_state()
            return

        for station in self._candidates():
            if _candidate_label(station) == option:
                node_id = _station_node_id(station)
                if node_id:
                    await self.coordinator.async_set_favourite(node_id)
                    self.async_write_ha_state()
                    return

        _LOGGER.warning(f"Could not resolve selected option '{option}' to a station")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return descriptive info about the favourite station (no price data)."""
        node_id = self.coordinator.favourite_node_id
        favourite_station = (self.coordinator.data or {}).get("favourite_station")

        if not favourite_station or not node_id:
            return {"node_id": node_id}

        location = favourite_station.get("location") or {}
        return {
            "node_id": node_id,
            "name": favourite_station.get("trading_name") or favourite_station.get("name"),
            "brand": favourite_station.get("brand_name"),
            "postcode": location.get("postcode"),
            "distance_miles": favourite_station.get("distance"),
            "distance_driving_miles": favourite_station.get("distance_driving"),
            "fuel_types": favourite_station.get("fuel_types"),
        }
