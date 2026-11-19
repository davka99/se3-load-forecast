import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_pipeline.py"


def load():
    spec = importlib.util.spec_from_file_location("run_pipeline", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_unknown_stage_returns_error_code():
    assert load().main(["nope"]) == 2


def test_no_stage_returns_usage():
    assert load().main([]) == 2
