"""The Integration E-ST integration."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .updater import Updater
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up E-ST Integration from a config entry."""
    
    poll_interval = config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    updater = Updater(hass, config_entry)

    hass.async_create_task(updater.async_update())

    # Set up daily update
    async def update_callback(_):
        await updater.async_update()

    async_track_time_interval(hass, update_callback, timedelta(hours=poll_interval))

    return True
