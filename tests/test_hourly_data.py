from __future__ import annotations

from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys

ENERGY_STORAGE_PATH = Path(__file__).parents[1] / "custom_components" / "tauron_amiplus" / "energy_storage.py"
energy_spec = importlib.util.spec_from_file_location("energy_storage_for_hourly", ENERGY_STORAGE_PATH)
energy_storage = importlib.util.module_from_spec(energy_spec)
sys.modules[energy_spec.name] = energy_storage
assert energy_spec.loader
energy_spec.loader.exec_module(energy_storage)
from pathlib import Path
import importlib.util
import json
import sys
import sys

MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "tauron_amiplus" / "hourly_data.py"
spec = importlib.util.spec_from_file_location("hourly_data", MODULE_PATH)
hourly_data = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hourly_data
assert spec.loader
spec.loader.exec_module(hourly_data)


def load_fixture():
    with (Path(__file__).parents[1] / "scripts" / "tauron_raw_data.json").open(encoding="utf-8") as stream:
        return json.load(stream)


def test_all_measurement_types_and_values_match():
    payload = load_fixture()
    for measurement_type in hourly_data.MEASUREMENT_TYPES:
        parsed = hourly_data.parse_daily_endpoint(
            measurement_type,
            date(2023, 1, 1),
            payload["data"]["2023-01-01"][measurement_type],
        )
        assert parsed is not None
        assert len(parsed.readings) == 24
        assert parsed.diagnostics.values_mismatches == 0


def test_hour_is_source_of_timestamp_and_date_is_not_repeated_timestamp():
    payload = load_fixture()
    parsed = hourly_data.parse_daily_endpoint("consum", date(2023, 1, 1), payload["data"]["2023-01-01"]["consum"])
    assert parsed.readings[0].timestamp.hour == 0
    assert parsed.readings[-1].timestamp.hour == 23
    assert len({reading.timestamp.isoformat() for reading in parsed.readings}) == 24


def test_dst_transition_reports_missing_and_duplicate_hours():
    payload = load_fixture()
    spring = hourly_data.parse_daily_endpoint("consum", date(2023, 3, 26), payload["data"]["2023-03-26"]["consum"])
    autumn = hourly_data.parse_daily_endpoint("consum", date(2023, 10, 29), payload["data"]["2023-10-29"]["consum"])
    assert spring.diagnostics.missing_hours == 1
    assert autumn.diagnostics.duplicate_hours == 1
    assert len(spring.readings) == 23
    assert len(autumn.readings) == 25


def test_status_100_is_kept_and_reported():
    payload = load_fixture()
    for day, endpoints in payload["data"].items():
        parsed = hourly_data.parse_daily_endpoint("consum", date.fromisoformat(day), endpoints["consum"])
        if parsed and parsed.diagnostics.status_counts.get("100"):
            assert any(reading.status == 100 for reading in parsed.readings)
            return
    raise AssertionError("Fixture has no status 100")


def test_energy_totals_and_consistency_metrics():
    totals = energy_storage.calculate_energy_totals(
        [{"EC": "10"}], [{"EC": "8"}], [{"EC": "6"}], [{"EC": "2"}]
    )
    assert totals.self_consumption_from_production == Decimal("6")
    assert totals.self_consumption_from_load == Decimal("4")
    assert totals.grid_balance == Decimal("4")
    assert totals.self_consumption_share == Decimal("75")


def test_raw_payload_parses_four_series():
    result = hourly_data.parse_raw_payload(load_fixture())
    assert set(result) == set(hourly_data.MEASUREMENT_TYPES)
    assert all(result[key] for key in hourly_data.MEASUREMENT_TYPES)


def test_hour_start_ignores_time_part_of_date():
    from datetime import timedelta
    rows = [{"Date": "2026-10-21T02:00:00.000+02:00", "Hour": str(h)} for h in range(1, 25)]
    starts = [hourly_data.hour_start(r) for r in rows]
    assert len(set(starts)) == 24
    assert all(b - a == timedelta(hours=1) for a, b in zip(starts, starts[1:]))
    assert starts[0].astimezone(hourly_data.WARSAW).isoformat() == "2026-10-21T00:00:00+02:00"
    assert starts[-1].astimezone(hourly_data.WARSAW).isoformat() == "2026-10-21T23:00:00+02:00"


def test_hour_start_dst_day_has_25_distinct_hours():
    starts = {hourly_data.hour_start({"Date": "2026-10-25", "Hour": h}) for h in range(1, 26)}
    assert len(starts) == 25
    assert hourly_data.hour_start({"Date": None, "Hour": 1}) is None
    assert hourly_data.hour_start({"Date": "2026-10-25", "Hour": 99}) is None
