"""The E-ST integration."""

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

# The domain of your integration. Should be equal to the name of your integration directory.
DOMAIN = "e_st"

# List of platforms that you want to support.
PLATFORMS = ["sensor"]

# Logger
_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the E-ST integration."""
    # Nothing to set up here, configuration is done via config flow.
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up E-ST from a config entry."""
    # Forward the setup to the sensor platform.
    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(entry, "sensor")
    )
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload E-ST config entry."""
    # Unload entities for this entry.
    await asyncio.gather(
        *[hass.config_entries.async_forward_entry_unload(entry, platform) for platform in PLATFORMS]
    )
    return True