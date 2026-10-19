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


def test_fifo_state_round_trips():
    original = [Batch("2024-01", 1.25, date(2025, 2, 1), "source", True)]
    restored = deserialize_batches(serialize_batches(original))
    assert restored == original
