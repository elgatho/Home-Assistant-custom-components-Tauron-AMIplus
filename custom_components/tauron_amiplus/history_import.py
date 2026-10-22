from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.components.recorder.models import StatisticMeanType, StatisticMetaData
from homeassistant.const import UnitOfEnergy
from homeassistant.util.dt import as_utc


_LOGGER = logging.getLogger(__name__)
STATISTICS_SOURCE = "tauron_amiplus"


def statistic_id(meter_id: str, measurement_type: str) -> str:
    return f"{STATISTICS_SOURCE}:{meter_id}_{measurement_type}"


def build_metadata(name: str, identifier: str) -> StatisticMetaData:
    return {
        "has_mean": False,
        "has_sum": True,
        "name": name,
        "source": STATISTICS_SOURCE,
        "statistic_id": identifier,
        "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
        "mean_type": StatisticMeanType.NONE,
        "unit_class": "energy",
    }


async def async_get_statistics_readings(hass, statistic_id_value: str, start: datetime.datetime, end: datetime.datetime) -> list[dict[str, Any]]:
    result = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        as_utc(start),
        as_utc(end),
        {statistic_id_value},
        "hour",
        None,
        {"state", "sum"},
    )
    rows = result.get(statistic_id_value, []) if isinstance(result, dict) else []
    readings = []
    for row in rows:
        if row.get("state") is None or row.get("start") is None:
            continue
        start_value = row["start"]
        if isinstance(start_value, (int, float)):
            start_value = datetime.datetime.fromtimestamp(start_value, datetime.timezone.utc)
        readings.append({"Date": start_value.isoformat(), "EC": row["state"]})
    return readings
