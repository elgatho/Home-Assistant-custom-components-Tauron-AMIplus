from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

try:
    from .hourly_data import parse_raw_payload
except ImportError:
    import importlib.util
    _hourly_spec = importlib.util.spec_from_file_location("hourly_data", Path(__file__).with_name("hourly_data.py"))
    _hourly_module = importlib.util.module_from_spec(_hourly_spec)
    import sys
    sys.modules[_hourly_spec.name] = _hourly_module
    assert _hourly_spec.loader
    _hourly_spec.loader.exec_module(_hourly_module)
    parse_raw_payload = _hourly_module.parse_raw_payload


@dataclass
class Batch:
    period: str
    available: float
    expires: date
    source: str = "tauron_amiplus_monthly"
    verified: bool = False


@dataclass(frozen=True)
class EnergyTotals:
    consumption: Decimal
    production: Decimal
    grid_import: Decimal
    grid_export: Decimal

    @property
    def self_consumption_from_production(self) -> Decimal:
        return self.production - self.grid_export

    @property
    def self_consumption_from_load(self) -> Decimal:
        return self.consumption - self.grid_import

    @property
    def grid_balance(self) -> Decimal:
        return self.grid_import - self.grid_export

    @property
    def self_consumption_share(self) -> Decimal | None:
        if self.production == 0:
            return None
        return self.self_consumption_from_production / self.production * 100


def _reading_value(reading: dict[str, Any]) -> Decimal | None:
    try:
        value = Decimal(str(reading.get("EC")))
    except (TypeError, ValueError):
        return None
    return value if value.is_finite() and value >= 0 else None


def calculate_energy_totals(
    consumption: list[dict[str, Any]],
    production: list[dict[str, Any]],
    grid_import: list[dict[str, Any]],
    grid_export: list[dict[str, Any]],
) -> EnergyTotals:
    def total(readings: list[dict[str, Any]]) -> Decimal:
        return sum((_reading_value(reading) or Decimal("0") for reading in readings), Decimal("0"))
    return EnergyTotals(total(consumption), total(production), total(grid_import), total(grid_export))


def _expire_batches(batches: list[Batch], current: date) -> None:
    batches[:] = [batch for batch in batches if batch.expires > current and batch.available > 1e-9]


def apply_credit_fifo(batches: list[Batch], grid_export: float, grid_import: float, factor: float = 0.8) -> tuple[float, float]:
    if factor < 0:
        raise ValueError("factor must not be negative")
    credit = max(0.0, grid_export) * factor
    batches.append(Batch("current", credit, date.max, source="calculated_export"))
    uncovered = apply_fifo(batches, grid_import)
    batches[:] = [batch for batch in batches if batch.available > 1e-9]
    return uncovered, sum(batch.available for batch in batches)


def serialize_batches(batches: list[Batch]) -> list[dict[str, Any]]:
    return [{"period": b.period, "available": round(b.available, 9), "expires": b.expires.isoformat(), "source": b.source, "verified": b.verified} for b in batches]


def deserialize_batches(items: list[dict[str, Any]]) -> list[Batch]:
    batches = []
    for item in items:
        try:
            available = float(item["available"])
            expires = date.fromisoformat(item["expires"])
        except (KeyError, TypeError, ValueError):
            continue
        if available > 1e-9:
            batches.append(Batch(str(item.get("period", "unknown")), available, expires, str(item.get("source", "restored")), bool(item.get("verified", False))))
    return batches


def month_after(year: int, month: int, months: int) -> date:
    index = year * 12 + month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def load_raw_hourly(path: str | Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    series = parse_raw_payload(payload)
    export = [{"Date": reading.timestamp.isoformat(), "EC": str(reading.value), "Status": reading.status} for reading in series["netto_oze"]]
    grid_import = [{"Date": reading.timestamp.isoformat(), "EC": str(reading.value), "Status": reading.status} for reading in series["netto"]]
    tariffs = {str(reading.metadata.get("Taryfa")) for reading in series["netto"] if reading.metadata.get("Taryfa")}
    return export, grid_import, ",".join(sorted(tariffs)) or "unknown"


def load_normalized_raw(path: str | Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    raw_path = Path(path)
    with raw_path.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    hourly = payload.get("hourly", [])
    if not isinstance(hourly, list):
        raise ValueError("Normalized Tauron RAW file has no hourly list")
    generation = []
    consumption = []
    tariffs: set[str] = set()
    for reading in hourly:
        if not isinstance(reading, dict):
            continue
        tariff = reading.get("tariff")
        if tariff:
            tariffs.add(str(tariff))
        item = {"Date": reading.get("timestamp"), "EC": reading.get("kwh")}
        if reading.get("kind") == "generation":
            generation.append(item)
        elif reading.get("kind") == "consumption":
            consumption.append(item)
    tariff = ",".join(sorted(tariffs)) or "unknown"
    return generation, consumption, tariff


def apply_fifo(batches: list[Batch], consumption: float) -> float:
    remaining = max(0.0, consumption)
    for batch in batches:
        used = min(batch.available, remaining)
        batch.available -= used
        remaining -= used
        if remaining <= 1e-9:
            break
    return remaining


def calculate_fifo_periods(
    monthly: dict[str, dict[str, float]],
    today: date,
    factor: float = 0.8,
) -> tuple[list[Batch], float]:
    batches: list[Batch] = []
    uncovered = 0.0
    for period in sorted(monthly):
        year, month = (int(part) for part in period.split("-"))
        expires = month_after(year, month, 12)
        _expire_batches(batches, date(year, month, 1))
        batches.append(Batch(period, monthly[period].get("export", 0.0) * factor, expires))
        uncovered += apply_fifo(batches, monthly[period].get("import", 0.0))
    _expire_batches(batches, today)
    return batches, uncovered


def calculate_monthly_window(
    generation_data: list[dict[str, Any]],
    consumption_data: list[dict[str, Any]],
    today: date,
    completed_months: int = 12,
    settlement_factor: float = 0.8,
    window_start: date | None = None,
) -> tuple[dict[str, Any], float, str]:
    if completed_months < 1:
        raise ValueError("completed_months must be positive")
    if not 0 <= settlement_factor <= 1:
        raise ValueError("settlement_factor must be between 0 and 1")

    current_month = month_after(today.year, today.month, 1)
    # A fixed window_start pins the first accounted month (energy_storage_reset_date) and
    # excludes everything before it; without it, fall back to a rolling window.
    if window_start is not None:
        first_month = date(window_start.year, window_start.month, 1)
    else:
        first_month = month_after(today.year, today.month, -completed_months)
    generation_by_period: dict[str, float] = {}
    consumption_by_period: dict[str, float] = {}
    for item, target in ((generation_data, generation_by_period), (consumption_data, consumption_by_period)):
        for reading in item:
            period = str(reading.get("Date", ""))[:7]
            value = reading.get("EC")
            if len(period) != 7 or value is None:
                continue
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                continue
            if numeric_value < 0:
                continue
            try:
                year, month = (int(part) for part in period.split("-"))
                period_start = date(year, month, 1)
            except (TypeError, ValueError):
                continue
            if not first_month <= period_start < current_month:
                continue
            target[period] = target.get(period, 0.0) + numeric_value
    periods = sorted(set(generation_by_period) | set(consumption_by_period))
    batches: list[Batch] = []
    uncovered_consumption = 0.0
    for period in periods:
        year, month = (int(part) for part in period.split("-"))
        expires = month_after(year, month, 12)
        if expires > today and generation_by_period.get(period, 0.0) > 0:
            batches.append(Batch(period, generation_by_period[period] * settlement_factor, expires))
        uncovered_consumption += apply_fifo(batches, consumption_by_period.get(period, 0.0))
    state = {
        "period": today.strftime("%Y-%m"),
        "window_start": first_month.isoformat(),
        "window_end": current_month.isoformat(),
        "generation": round(sum(generation_by_period.values()), 6),
        "consumption": round(sum(consumption_by_period.values()), 6),
        "uncovered_consumption": round(uncovered_consumption, 6),
        "batches": [
            {
                "period": batch.period,
                "available": round(batch.available, 6),
                "expires": batch.expires.isoformat(),
                "source": "tauron_amiplus_last_12_months_hourly",
                "verified": False,
            }
            for batch in batches
            if batch.available > 1e-9
        ],
    }
    return state, max(0.0, round(sum(batch.available for batch in batches), 6)), "approximate_12_month_hourly_fifo"


def calculate_monthly_statistics(
    generation_statistics: list[dict[str, Any]],
    consumption_statistics: list[dict[str, Any]],
    today: date,
) -> tuple[dict[str, Any], float, str]:
    consumption_by_period = {
        item["period"]: float(item["value"])
        for item in consumption_statistics
        if item.get("period") and item.get("value") is not None
    }
    batches: list[Batch] = []
    generation_periods = set()
    for item in generation_statistics:
        period = item.get("period")
        value = item.get("value")
        if not period or value is None or float(value) < 0:
            raise ValueError("invalid monthly statistic")
        generation_periods.add(period)
        year, month = (int(part) for part in period.split("-"))
        batch = Batch(period, float(value) * 0.8, month_after(year, month, 12))
        if batch.expires > today and batch.available > 0:
            batches.append(batch)
    uncovered_consumption = 0.0
    for period in sorted(consumption_by_period):
        uncovered_consumption += apply_fifo(batches, consumption_by_period[period])

    for period, value in consumption_by_period.items():
        if period not in generation_periods:
            uncovered_consumption += max(0.0, value)
    state = {
        "period": today.strftime("%Y-%m"),
        "generation": round(sum(float(item.get("value", 0.0)) for item in generation_statistics), 6),
        "consumption": round(sum(consumption_by_period.values()), 6),
        "uncovered_consumption": round(uncovered_consumption, 6),
        "batches": [
            {
                "period": batch.period,
                "available": round(batch.available, 6),
                "expires": batch.expires.isoformat(),
                "source": "recorder_monthly_statistics",
                "verified": False,
            }
            for batch in batches
            if batch.available > 1e-9
        ],
    }
    return state, max(0.0, round(sum(batch.available for batch in batches), 6)), "approximate_12_month_monthly_fifo"


def calculate_state(
    state: dict[str, Any] | None,
    period: str,
    generation: float,
    consumption: float,
    initial_balance: float,
    today: date,
) -> tuple[dict[str, Any], float, str]:
    if generation < 0 or consumption < 0:
        raise ValueError("negative monthly reading")
    previous = state or {}
    previous_period = previous.get("period")
    previous_generation = previous.get("generation")
    previous_consumption = previous.get("consumption")
    batches = [
        Batch(
            item["period"],
            float(item["available"]),
            date.fromisoformat(item["expires"]),
            item.get("source", "tauron_amiplus_monthly"),
            bool(item.get("verified", False)),
        )
        for item in previous.get("batches", [])
    ]
    batches = [batch for batch in batches if batch.expires > today and batch.available > 1e-9]
    if not previous:
        if initial_balance > 0:
            batches.append(Batch("initial", initial_balance, date.max, "confirmed_initial_balance", True))
        generation_delta = generation
        consumption_delta = consumption
        previous_period = period
    elif period != previous_period:
        generation_delta = generation
        consumption_delta = consumption
        previous_period = period
    else:
        generation_delta = generation - float(previous_generation)
        consumption_delta = consumption - float(previous_consumption)
        if generation_delta < -1e-9 or consumption_delta < -1e-9:
            raise ValueError("monthly counter correction or reset requires synchronization")
    if generation_delta > 0:
        year, month = (int(value) for value in period.split("-"))
        batches.append(Batch(period, generation_delta * 0.8, month_after(year, month, 12)))
    uncovered_consumption = apply_fifo(batches, consumption_delta)
    previous_generation = generation
    previous_consumption = consumption
    result = {
        "period": previous_period,
        "generation": previous_generation,
        "consumption": previous_consumption,
        "uncovered_consumption": round(uncovered_consumption, 6),
        "batches": [
            {
                "period": batch.period,
                "available": round(batch.available, 6),
                "expires": batch.expires.isoformat(),
                "source": batch.source,
                "verified": batch.verified,
            }
            for batch in batches
            if batch.available > 1e-9
        ],
    }
    return result, round(sum(batch.available for batch in batches), 6), "approximate_monthly_fifo"
