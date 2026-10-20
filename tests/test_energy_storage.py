from datetime import date
import importlib.util
from pathlib import Path
import sys

_MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "tauron_amiplus" / "energy_storage.py"
_spec = importlib.util.spec_from_file_location("energy_storage", _MODULE_PATH)
energy_storage = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = energy_storage
assert _spec.loader
_spec.loader.exec_module(energy_storage)

Batch = energy_storage.Batch
apply_credit_fifo = energy_storage.apply_credit_fifo
calculate_monthly_window = energy_storage.calculate_monthly_window
deserialize_batches = energy_storage.deserialize_batches
serialize_batches = energy_storage.serialize_batches


def test_credit_fifo_uses_oldest_batches_and_does_not_reuse_credit():
    batches = [
        Batch("2024-01", 5, date(2025, 2, 1)),
        Batch("2024-02", 10, date(2025, 3, 1)),
    ]
    uncovered, remaining = apply_credit_fifo(batches, 0, 7, 0.8)
    assert uncovered == 0
    assert remaining == 8
    assert batches[0].period == "2024-02"
    assert batches[0].available == 8


def test_fifo_expiry_and_factor_are_configurable():
    state, value, _ = calculate_monthly_window(
        [{"Date": "2024-01-15T00:00:00+01:00", "EC": "10"}],
        [{"Date": "2024-01-15T00:00:00+01:00", "EC": "2"}],
        date(2025, 2, 1),
        completed_months=24,
        settlement_factor=0.8,
    )
    assert value == 0
    assert state["batches"] == []


def test_raw_loader_uses_netto_series(tmp_path):
    import json

    payload = {
        "data": {
            "2026-09-01": {
                "netto": {"raw_response": json.dumps({"data": {"allData": [{"EC": "2", "Hour": "1"}]}})},
                "netto_oze": {"raw_response": json.dumps({"data": {"allData": [{"EC": "5", "Hour": "1"}]}})},
                "consum": {"raw_response": json.dumps({"data": {"allData": [{"EC": "99", "Hour": "1"}]}})},
                "oze": {"raw_response": json.dumps({"data": {"allData": [{"EC": "99", "Hour": "1"}]}})},
            }
        }
    }
    path = tmp_path / "tauron_raw_data.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    export, grid_import, _ = energy_storage.load_raw_hourly(path)
    assert export[0]["EC"] == "5"
    assert grid_import[0]["EC"] == "2"


def test_reset_period_matches_september_and_october_surplus():
    state, value, _ = calculate_monthly_window(
        [
            {"Date": "2026-09-01T00:00:00+02:00", "EC": "615.319"},
            {"Date": "2026-10-01T00:00:00+02:00", "EC": "362.361"},
        ],
        [
            {"Date": "2026-09-01T00:00:00+02:00", "EC": "405.036"},
            {"Date": "2026-10-01T00:00:00+02:00", "EC": "213.374"},
        ],
        date(2026, 10, 20),
        completed_months=2,
        settlement_factor=0.8,
    )
    assert round(value, 3) == 163.734
    assert round(state["generation"], 3) == 977.680
    assert round(state["consumption"], 3) == 618.410


SEP = "2025-09-15T00:00:00+02:00"
AUG = "2025-08-15T00:00:00+02:00"


def test_window_start_excludes_data_before_reset_date():
    # A 1000 kWh export in Aug 2025 must be ignored; only the 100 kWh Sep export counts (*0.8).
    state, value, _ = calculate_monthly_window(
        [{"Date": AUG, "EC": "1000"}, {"Date": SEP, "EC": "100"}],
        [],
        date(2025, 9, 20),
        window_start=date(2025, 9, 1),
        settlement_factor=0.8,
    )
    assert value == 80
    assert state["generation"] == 100
    assert state["window_start"] == "2025-09-01"


def test_initial_balance_is_zero_no_seeded_credit():
    # Consumption with no prior export => balance stays 0, nothing is injected from history/cache.
    state, value, _ = calculate_monthly_window(
        [],
        [{"Date": SEP, "EC": "50"}],
        date(2025, 9, 20),
        window_start=date(2025, 9, 1),
    )
    assert value == 0
    assert state["uncovered_consumption"] == 50
    assert state["batches"] == []


def test_recompute_is_idempotent_no_double_counting():
    # The sensor fully recomputes from the recorder each update; repeated calls must not accumulate.
    args = ([{"Date": SEP, "EC": "100"}], [{"Date": SEP, "EC": "30"}], date(2025, 9, 20))
    kwargs = {"window_start": date(2025, 9, 1), "settlement_factor": 0.8}
    first = calculate_monthly_window(*args, **kwargs)[1]
    second = calculate_monthly_window(*args, **kwargs)[1]
    assert first == second == 50  # 100*0.8 - 30


def test_surplus_expires_12_months_after_creation_month():
    # Sep 2025 surplus is gone once the 12-month FIFO window rolls past it.
    _, value, _ = calculate_monthly_window(
        [{"Date": SEP, "EC": "100"}],
        [],
        date(2026, 10, 20),
        window_start=date(2025, 9, 1),
    )
    assert value == 0


def test_fifo_state_round_trips():
    original = [Batch("2024-01", 1.25, date(2025, 2, 1), "source", True)]
    restored = deserialize_batches(serialize_batches(original))
    assert restored == original
