import datetime
import logging

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticMetaData, StatisticMeanType
from homeassistant.components.recorder.statistics import (async_add_external_statistics, get_last_statistics,
                                                          statistics_during_period)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import as_utc, get_time_zone, utc_from_timestamp

from .connector import TauronAmiplusConnector, TauronAmiplusRawData
from .const import (CONF_METER_ID, CONF_METER_NAME, CONF_SHOW_BALANCED, CONF_SHOW_GENERATION, CONST_BALANCED,
                    CONST_CONSUMPTION, CONST_GENERATION, DEFAULT_NAME, STATISTICS_DOMAIN)

_LOGGER = logging.getLogger(__name__)
MAX_AUTOMATIC_STATISTICS_DAYS = 7


class TauronAmiplusStatisticsUpdater:

    def __init__(self, hass: HomeAssistant, connector: TauronAmiplusConnector, meter_id: str, meter_name: str,
                 show_generation: bool, show_balanced: bool) -> None:
        self.hass = hass
        self.connector = connector
        self.meter_id = meter_id
        self.meter_name = meter_name
        self.show_generation = show_generation
        self.show_balanced = show_balanced

    @staticmethod
    async def manually_update(hass, start_date: datetime.date, entry) -> None:
        username = entry.data[CONF_USERNAME]
        password = entry.data[CONF_PASSWORD]
        meter_id = entry.data[CONF_METER_ID]
        meter_name = entry.data[CONF_METER_NAME]

        show_generation = entry.options.get(CONF_SHOW_GENERATION, False)
        show_balanced = entry.options.get(CONF_SHOW_BALANCED, False)

        connector = TauronAmiplusConnector(username, password, meter_id, hass=hass, config_entry_id=entry.entry_id, show_generation=show_generation,
                                           show_balanced=show_balanced)
        statistics_updater = TauronAmiplusStatisticsUpdater(hass, connector, meter_id, meter_name, show_generation, show_balanced)

        data = await connector.get_raw_data()
        start_date = datetime.datetime.combine(start_date,
                                               datetime.time(tzinfo=datetime.datetime.now().astimezone().tzinfo))
        if data is not None:
            await statistics_updater.update_all(data, start_date)

    async def update_all(self, last_data: TauronAmiplusRawData, start_date: datetime.datetime = None) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        zones = self._zones_from_data(last_data)
        all_stat_ids = await self.prepare_stats_ids(zones)

        if (start_date is not None
                or not all(self.are_stats_up_to_date(v["last_stats_end"]) for v in all_stat_ids.values())):
            if start_date is None:
                stored_ends = [v["last_stats_end"] for v in all_stat_ids.values() if v["last_stats_end"] is not None]
                start_range = max(
                    min(stored_ends, default=now - datetime.timedelta(days=MAX_AUTOMATIC_STATISTICS_DAYS)),
                    now - datetime.timedelta(days=MAX_AUTOMATIC_STATISTICS_DAYS),
                )
                start_range = start_range.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                start_range = start_date
            data_consumption = await self.connector.get_raw_values_daily_for_range(start_range, now, False)
            raw_data = {CONST_CONSUMPTION: data_consumption["data"]["allData"] if data_consumption else []}
            if data_consumption:
                zones = data_consumption["data"].get("zonesName", zones)
            if self.show_generation or self.show_balanced:
                data_generation = await self.connector.get_raw_values_daily_for_range(start_range, now, True)
                if not data_generation:
                    return
                raw_data[CONST_GENERATION] = data_generation["data"]["allData"]
        else:
            return

        if not raw_data[CONST_CONSUMPTION]:
            return

        if self.show_balanced:
            balanced_consumption, balanced_generation = self.prepare_balanced_raw_data(raw_data)
            raw_data[f"{CONST_BALANCED}_{CONST_CONSUMPTION}"] = balanced_consumption
            raw_data[f"{CONST_BALANCED}_{CONST_GENERATION}"] = balanced_generation

        for s, v in all_stat_ids.items():
            if v["last_stats_end"] is not None:
                stat = await self.get_stats(raw_data[v["data_source"]], s)
                if not stat.get(s):
                    # No stored statistics within the fetched range: continue from the last stored one.
                    stat = await self.get_last_stats(s)
                if not stat.get(s):
                    continue
                v["sum"] = stat[s][0]["sum"]
                start = stat[s][0].get("start", v["last_stats_end"])
                if isinstance(start, float):
                    start = utc_from_timestamp(start)
                if start_date is not None and start > start_date:
                    start = None
                    v["sum"] = 0
                v["last_stats_time"] = start

        for s, v in all_stat_ids.items():
            await self.update_stats(s, v["name"], v["sum"], v["last_stats_time"], v["zone"], raw_data[v["data_source"]])

    @staticmethod
    def _zones_from_data(data: TauronAmiplusRawData) -> dict:
        if data.consumption is None or not data.consumption.json_daily:
            return {}
        return data.consumption.json_daily.get("data", {}).get("zonesName", {})

    async def prepare_stats_ids(self, zones):
        suffixes = [{
            "id": CONST_CONSUMPTION,
            "name": CONST_CONSUMPTION,
            "data": CONST_CONSUMPTION,
            "zone": None
        }]
        if self.show_generation:
            suffixes.append({
                "id": CONST_GENERATION,
                "name": CONST_GENERATION,
                "data": CONST_GENERATION,
                "zone": None
            })
        if self.show_balanced:
            suffixes.append({
                "id": f"{CONST_BALANCED}_{CONST_CONSUMPTION}",
                "name": f"{CONST_BALANCED} {CONST_CONSUMPTION}",
                "data": f"{CONST_BALANCED}_{CONST_CONSUMPTION}",
                "zone": None
            })
            suffixes.append({
                "id": f"{CONST_BALANCED}_{CONST_GENERATION}",
                "name": f"{CONST_BALANCED} {CONST_GENERATION}",
                "data": f"{CONST_BALANCED}_{CONST_GENERATION}",
                "zone": None
            })
        if len(zones) > 1:
            for s in [*suffixes]:
                suffixes.extend([
                    {
                        "id": f'{s["id"]}_zone_{zone}',
                        "name": f'{s["name"]} {zone_name}',
                        "data": s["data"],
                        "zone": zone
                    }
                    for zone, zone_name in zones.items()
                ])

        all_stat_ids = {
            self.get_stats_id(s["id"]): {
                "name": self.get_stats_name(s["name"]),
                "zone": s["zone"],
                "data_source": s["data"],
                "sum": 0,
                "last_stats_time": None,
                "last_stats_end": None
            }
            for s in suffixes
        }
        for k, v in all_stat_ids.items():
            v["last_stats_end"] = await self.get_last_stats_date(k)
        return all_stat_ids

    def get_stats_id(self, suffix):
        return f"{STATISTICS_DOMAIN}:{self.meter_id}_{suffix}".lower()

    def get_stats_name(self, suffix):
        return f"{DEFAULT_NAME} {self.meter_name} {suffix}"

    @staticmethod
    def are_stats_up_to_date(last_stats_end):
        if last_stats_end is None:
            return False
        now = datetime.datetime.now()
        return (as_utc(now) - last_stats_end).days < 30

    @staticmethod
    def prepare_balanced_raw_data(raw_data) -> (dict, dict):
        consumption_data = raw_data[CONST_CONSUMPTION]
        generation_data = raw_data[CONST_GENERATION]
        if len(consumption_data) != len(generation_data):
            return {}, {}
        balanced_consumption = []
        balanced_generation = []

        for consumption, generation in zip(consumption_data, generation_data):
            value_consumption = float(consumption["EC"])
            value_generation = float(generation["EC"])
            balance = value_consumption - value_generation
            if balance > 0:
                output_consumption = {
                    "EC": f'{balance}',
                    "Date": consumption["Date"],
                    "Hour": consumption["Hour"],
                    "Zone": consumption["Zone"],
                }
                output_generation = {
                    "EC": "0",
                    "Date": generation["Date"],
                    "Hour": generation["Hour"],
                    "Zone": generation["Zone"],
                }
            else:
                output_consumption = {
                    "EC": "0",
                    "Date": consumption["Date"],
                    "Hour": consumption["Hour"],
                    "Zone": consumption["Zone"],
                }
                output_generation = {
                    "EC": f'{-balance}',
                    "Date": generation["Date"],
                    "Hour": generation["Hour"],
                    "Zone": generation["Zone"],
                }
            balanced_consumption.append(output_consumption)
            balanced_generation.append(output_generation)

        return balanced_consumption, balanced_generation

    async def update_stats(self, statistic_id, statistic_name, initial_sum, last_stats_time, zone_id, raw_data):
        current_sum = initial_sum
        metadata: StatisticMetaData = {
            "has_mean": False,
            "has_sum": True,
            "name": statistic_name,
            "source": STATISTICS_DOMAIN,
            "statistic_id": statistic_id,
            "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
            "mean_type": StatisticMeanType.NONE,
            "unit_class": "energy",
        }
        statistic_data = []
        for raw_hour in raw_data:
            start = self.get_time(raw_hour)
            if last_stats_time is not None and start <= last_stats_time:
                continue
            usage = float(raw_hour["EC"])
            if zone_id is not None and raw_hour["Zone"] != zone_id:
                usage = 0
            current_sum += usage
            stats = {
                "start": start,
                "state": usage,
                "sum": current_sum
            }
            statistic_data.append(stats)
        async_add_external_statistics(self.hass, metadata, statistic_data)
        self.log(f"Updated {len(statistic_data)} entries for statistic: {statistic_id} ")

    async def get_last_stats_date(self, statistic_id):
        last_stats = await self.get_last_stats(statistic_id)
        if statistic_id in last_stats and len(last_stats[statistic_id]) > 0:
            end = last_stats[statistic_id][0]["end"]
            if isinstance(end, float):
                end = utc_from_timestamp(end)
            return end
        return None

    async def get_last_stats(self, statistic_id):
        return await get_instance(self.hass).async_add_executor_job(
            get_last_statistics,
            self.hass, 1, statistic_id, True, {"state", "sum"})

    async def get_stats(self, raw_data, statistic_id):
        if not raw_data:
            return {statistic_id: [{"sum": 0, "start": None}]}
        start = self.get_time(raw_data[0])
        if start is None:
            return {statistic_id: [{"sum": 0, "start": None}]}
        return await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass, start, None, [statistic_id], "hour", None, {"state", "sum"})

    def log(self, msg):
        _LOGGER.debug(f"[{self.meter_id}]: {msg}")

    @staticmethod
    def get_time(raw_hour):
        zone = get_time_zone("Europe/Warsaw")
        value = raw_hour.get("Date")
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                day = datetime.date.fromisoformat(value[:10])
                hour = int(raw_hour.get("Hour", "")) - 1
                if not 0 <= hour <= 23:
                    return None
                return datetime.datetime.combine(day, datetime.time(hour), tzinfo=zone)
            except (TypeError, ValueError):
                return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
