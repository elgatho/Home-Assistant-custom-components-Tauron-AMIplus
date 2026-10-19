from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics, statistics_during_period
from homeassistant.const import UnitOfEnergy
from homeassistant.helpers.storage import Store
from homeassistant.util.dt import as_utc

from .hourly_data import MEASUREMENT_TYPES, TauronHourlyReading, parse_raw_payload

_LOGGER = logging.getLogger(__name__)
IMPORT_VERSION = 1
STATISTICS_SOURCE = "tauron_amiplus"


def statistic_id(meter_id: str, measurement_type: str) -> str:
    return f"{STATISTICS_SOURCE}:{meter_id}_{measurement_type}"


def _metadata(name: str, identifier: str) -> StatisticMetaData:
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


def _statistics(readings: list[TauronHourlyReading]) -> list[dict[str, Any]]:
    result = []
    cumulative = 0.0
    for reading in sorted(readings, key=lambda item: item.timestamp):
        cumulative += float(reading.value)
        result.append({"start": as_utc(reading.timestamp), "state": float(reading.value), "sum": cumulative})
    return result


def build_import_payload(payload: dict[str, Any], meter_id: str) -> dict[str, tuple[StatisticMetaData, list[dict[str, Any]]]]:
    series = parse_raw_payload(payload)
    return {
        measurement_type: (
            _metadata(f"Tauron AMIplus {measurement_type}", statistic_id(meter_id, measurement_type)),
            _statistics(series[measurement_type]),
        )
        for measurement_type in MEASUREMENT_TYPES
    }


async def async_import_raw_once(hass, meter_id: str, path: str | Path, *, force: bool = False) -> bool:
    _LOGGER.info("Starting Tauron historical import: meter=%s path=%s force=%s", meter_id, path, force)
    raw_path = Path(path)
    if not await asyncio.to_thread(raw_path.is_file):
        _LOGGER.error("Tauron historical import failed: file does not exist: %s", raw_path)
        return False
    raw_bytes = await asyncio.to_thread(raw_path.read_bytes)
    digest = hashlib.sha256(raw_bytes).hexdigest()
    store = Store(hass, IMPORT_VERSION, f"tauron_amiplus_history_{meter_id}")
    marker = await store.async_load()
    if marker and marker.get("sha256") == digest and marker.get("version") == IMPORT_VERSION and not force:
        _LOGGER.info("Tauron historical import skipped: already imported sha256=%s", digest[:12])
        return False
    try:
        payload = json.loads(raw_bytes)
        data = build_import_payload(payload, meter_id)
        for measurement_type, (metadata, statistics) in data.items():
            _LOGGER.info("Importing Tauron series=%s records=%d", measurement_type, len(statistics))
            if statistics:
                async_add_external_statistics(hass, metadata, statistics)
        await store.async_save({"version": IMPORT_VERSION, "sha256": digest, "imported_at": datetime.datetime.now().isoformat()})
    except Exception:
        _LOGGER.exception("Tauron historical import failed: meter=%s", meter_id)
        return False
    _LOGGER.info("Tauron historical import completed: meter=%s sha256=%s", meter_id, digest[:12])
    return True


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
