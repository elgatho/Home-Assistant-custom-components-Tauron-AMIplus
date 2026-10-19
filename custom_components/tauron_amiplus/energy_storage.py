from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


@dataclass
class Batch:
    period: str
    available: float
    expires: date
    source: str = "tauron_amiplus_monthly"
    verified: bool = False


def month_after(year: int, month: int, months: int) -> date:
    index = year * 12 + month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


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


def calculate_monthly_window(
    generation_data: list[dict[str, Any]],
    consumption_data: list[dict[str, Any]],
    today: date,
    completed_months: int = 12,
) -> tuple[dict[str, Any], float, str]:
    if completed_months < 1:
        raise ValueError("completed_months must be positive")

    current_month = date(today.year, today.month, 1)
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
            batches.append(Batch(period, generation_by_period[period] * 0.8, expires))
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
