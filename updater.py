"""E-ST integration using DataUpdateCoordinator."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional


import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.const import UnitOfEnergy
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import async_add_external_statistics, get_last_statistics


from .api import Api, ApiAuthException, Counter, DataPoint, Direction, GRANULARITY_HOUR
from .const import DOMAIN, CONF_EMAIL, CONF_PASSWORD

_LOGGER = logging.getLogger(__name__)


class Updater():
    counters: list[Counter]

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize updater."""

        self.hass = hass
        self.config_entry = config_entry
        self.api = Api(config_entry.data[CONF_EMAIL], config_entry.data[CONF_PASSWORD])
        self.counters = []
        self.time_zone = ZoneInfo("Europe/Riga")

    async def async_update(self):
        # add seonsors only on first run
        await self._set_counters()

        for counter in self.counters:
            await self.async_add_counter_statistics(counter)    

    async def async_add_counter_statistics(self, counter: Counter) -> None:
        _LOGGER.info(f"Adding counter statistics - counter: {counter.id}")

        consumed_statistic_id = self.get_statistic_id(counter, Direction.CONSUMED)
        returned_statistic_id = self.get_statistic_id(counter, Direction.RETURNED)
        today_start = datetime.now(self.time_zone).replace(hour=0, minute=0, second=0, microsecond=0)

        last_timestamp, consumption_sum = await self.async_get_last_statistic(consumed_statistic_id)
        _, returned_sum = await self.async_get_last_statistic(returned_statistic_id)

        if not last_timestamp:
            start_timestamp = await self._async_fetch_api(self.api.get_start_timestamp, counter.id)

            if not start_timestamp:
                _LOGGER.warning(f"No starting point - counter: {counter.id}")
                return

            last_timestamp = (datetime.fromtimestamp(start_timestamp, self.time_zone)
                .replace(hour=0, minute=0, second=0, microsecond=0)
                - timedelta(days=1)).timestamp()
        
        while True:
            #next iteration from next day
            start_date = (datetime.fromtimestamp(last_timestamp, self.time_zone)
                .replace(hour=0, minute=0, second=0, microsecond=0)
                + timedelta(days=1))
            
            _LOGGER.info(
                f"Interval - from: {start_date.strftime("%Y-%m-%d %H:%M:%S")}, to: {today_start.strftime("%Y-%m-%d %H:%M:%S")}, counter: {counter.id}"
            )

            if start_date >= today_start:
                _LOGGER.info(f"Start date greater than or equal to today - counter: {counter.id}")

                return

            data_points = await self._async_fetch_api(
                self.api.get_month_data,
                counter.id,
                start_date.year,
                start_date.month,
                GRANULARITY_HOUR,
            )

            if not data_points:
                break

            current_timestamp, consumption_sum = await self.async_add_direction_statistics(
                counter,
                Direction.CONSUMED,
                data_points[Direction.CONSUMED.value],
                last_timestamp,
                consumption_sum,
            )

            _, returned_sum = await self.async_add_direction_statistics(
                counter,
                Direction.RETURNED,
                data_points[Direction.RETURNED.value],
                last_timestamp,
                returned_sum,
            )

            # nothing has been added
            if current_timestamp == last_timestamp:
                _LOGGER.info(f"Nothing has been added - counter {counter.id}")
                return
            
            last_timestamp = current_timestamp

    async def async_add_direction_statistics(
        self,
        counter: Counter,
        direction: Direction,
        data_points: list[DataPoint],
        last_timestamp: int,
        sum: float,
    ) -> tuple[int, float]:
        stats = []
        metadata = {
            "source": DOMAIN,
            "name": self.get_statistic_name(counter, direction),
            "statistic_id": self.get_statistic_id(counter, direction),
            "has_sum": True,
            "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
        }

        _LOGGER.info(f"Gathering stats for {metadata["statistic_id"]}")

        current_timestamp = last_timestamp

        for point in data_points:
            dt = datetime.fromtimestamp(point.timestamp, self.time_zone) - timedelta(hours=1)
            point_timestamp = dt.timestamp()

            if current_timestamp >= point_timestamp:
                continue

            current_timestamp = point_timestamp
            sum += point.value

            stats.append({
                "start": dt,
                "sum": sum,
            })

        first = stats[0]
        last = stats[-1]

        if first:
            _LOGGER.debug(f"Stats added - id: {metadata["statistic_id"]}, from: {first["start"].strftime("%Y-%m-%d %H:%M:%S")} {first["sum"]}, to: {last["start"].strftime("%Y-%m-%d %H:%M:%S")} {last["sum"]}")
        else:
            _LOGGER.debug(f"No satats added - id: {metadata["statistic_id"]}")

        if (stats):
            async_add_external_statistics(self.hass, metadata, stats)

        return (current_timestamp, sum)

    async def async_get_last_statistic(self, statistic_id: str) -> tuple[Optional[int], Optional[float]]:
        """Get the last recorded timestamp and cumulative kWh for a given statistic_id.

        Returns:
            tuple: (last_timestamp, last_cumulative_kwh)
                   - last_timestamp: datetime of last saved record, or None if DB is empty
                   - last_cumulative_kwh: last cumulative value, or 0.0 if DB is empty
        """
        last_stats = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, True, {"sum"}
        )

        if last_stats and statistic_id in last_stats:
            last_record = last_stats[statistic_id][0]

            return (last_record["start"], last_record["sum"])

        return (None, 0.0)
    
    def get_statistic_name(self, counter: Counter, direction: Direction) -> str:
        return f"{counter.address} ({counter.id}) {direction.value}"
    
    def get_statistic_id(self, counter: Counter, direction: Direction) -> str:
        return f"{DOMAIN}:{counter.id}_{direction.value}"
    
    async def _set_counters(self) -> None:
        if self.counters:
            return

        self.counters = await self._async_fetch_api(self.api.get_counters)
    
    async def _async_fetch_api(self, func, *args, **kwargs):
        try:
            return await self.hass.async_add_executor_job(func, *args, **kwargs)
        except ApiAuthException as err:
            _LOGGER.error(err)
            raise UpdateFailed(err) from err
        except Exception as err:
            # This will show entities as unavailable by raising UpdateFailed exception
            raise UpdateFailed(f"Error communicating with API: {err}") from err
