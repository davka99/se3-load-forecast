import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_readme_numbers_match_results():
    if not (ROOT / "reports" / "backtest_results.json").exists():
        pytest.skip("results not generated yet")
    spec = importlib.util.spec_from_file_location("check_readme", ROOT / "scripts" / "check_readme.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main() == 0
