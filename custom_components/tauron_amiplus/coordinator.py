"""Update coordinator for TAURON sensors."""
import datetime
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .connector import TauronAmiplusConnector, TauronAmiplusRawData
from .const import (DEFAULT_UPDATE_INTERVAL, DOMAIN)
from .statistics import TauronAmiplusStatisticsUpdater

_LOGGER = logging.getLogger(__name__)


class TauronAmiplusUpdateCoordinator(DataUpdateCoordinator[TauronAmiplusRawData]):

    def __init__(
            self,
            hass: HomeAssistant,
            config_entry_id: str,
            username: str,
            password: str,
            meter_id: str,
            meter_name: str,
            show_generation: bool = False,
            show_12_months: bool = False,
            show_balanced: bool = False,
            show_balanced_year: bool = False,
            show_configurable: bool = False,
            show_configurable_date: datetime.date | None = None,
            store_statistics: bool = False,
            settlement_factor: float = 0.8,
            energy_storage_reset_date: datetime.date | None = None,
    ):
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_UPDATE_INTERVAL,
                         update_method=self.update_method)
        self.connector = TauronAmiplusConnector(username, password, meter_id, hass, config_entry_id, show_generation, show_12_months,
                                                show_balanced, show_balanced_year, show_configurable,
                                                show_configurable_date)
        self.meter_id = meter_id
        self.meter_name = meter_name
        self.show_generation = show_generation
        self.show_12_months = show_12_months
        self.show_balanced = show_balanced
        self.show_configurable = show_configurable
        self.show_configurable_date = show_configurable_date
        self.store_statistics = store_statistics
        self.settlement_factor = max(0.0, min(1.0, float(settlement_factor)))
        self.energy_storage_reset_date = energy_storage_reset_date
        self._last_tariff = None

    async def update_method(self) -> TauronAmiplusRawData:
        self.log("Starting data update")
        data = await self._update()
        self._sync_device_tariff(data)
        self.log("Downloaded current data; Recorder remains the statistics source")
        if data is not None and self.store_statistics:
            self.log("Starting incremental statistics update")
            await self.generate_statistics(data)
            self.log("Updated missing statistics")
        return data

    def _sync_device_tariff(self, data) -> None:
        """Keep the device's displayed tariff in line with the tariff detected in the latest data."""
        tariff = getattr(data, "tariff", None)
        if not tariff or tariff == "unknown" or tariff == self._last_tariff:
            return
        if self._last_tariff is not None:
            _LOGGER.info("[%s]: Tariff changed from %s to %s", self.meter_id, self._last_tariff, tariff)
        self._last_tariff = tariff
        registry = dr.async_get(self.hass)
        device = next((d for d in registry.devices.values() if (DOMAIN, self.meter_id) in d.identifiers), None)
        if device is not None:
            registry.async_update_device(device.id, sw_version=f"Tariff {tariff}")

    async def generate_statistics(self, data):
        statistics_updater = TauronAmiplusStatisticsUpdater(self.hass, self.connector, self.meter_id, self.meter_name,
                                                            self.show_generation, self.show_balanced)
        await statistics_updater.update_all(data)

    async def _update(self) -> TauronAmiplusRawData:
        return await self.connector.get_raw_data(include_statistics=False)

    def log(self, msg):
        _LOGGER.debug(f"[{self.meter_id}]: {msg}")
