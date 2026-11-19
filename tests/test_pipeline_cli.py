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


class _FakeResp:
    def __init__(self, payload=None, text=""):
        self._payload, self.text, self.status_code = payload, text, 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    """Answers eSett and SMHI URLs with fixture-shaped (made-up) data."""

    def __init__(self, stations, csv_text):
        self.stations, self.csv_text = stations, csv_text

    def get(self, url, params=None, timeout=None):
        if "esett" in url:
            return _FakeResp([
                {"timestampUTC": "2024-01-08T00:00:00Z", "total": -3000.0},
                {"timestampUTC": "2024-01-08T00:15:00Z", "total": -3001.0},
            ])
        if url.endswith("parameter/1.json"):
            return _FakeResp(self.stations)
        if url.endswith("data.csv"):
            return _FakeResp(text=self.csv_text)
        return _FakeResp({"value": [{"date": 1704682800000, "value": "-1.0", "quality": "Y"}]})


def test_fetch_stage_offline(tmp_path, monkeypatch):
    import json

    mod = load()
    cfg = mod.config
    stations = {"station": [{"key": str(sid), "name": name, "active": True}
                            for sid, (name, _) in cfg.TEMPERATURE_STATIONS.items()]}
    csv_text = (Path(__file__).parent / "fixtures" / "smhi_archive_sample.csv").read_text(
        encoding="utf-8")
    monkeypatch.setattr(mod, "_session", lambda: _FakeSession(stations, csv_text))
    monkeypatch.setattr(cfg, "DATA_RAW", tmp_path)
    monkeypatch.setattr(cfg, "START", "2024-01-08")
    monkeypatch.setattr(cfg, "END", "2024-01-09")
    monkeypatch.setattr(mod.esett.time, "sleep", lambda s: None)

    mod.fetch()

    manifest = json.loads((tmp_path / "fetch_manifest.json").read_text())
    assert manifest["esett"]["rows"] == 2
    assert manifest["esett"]["sign"]["negative"] == 2
    # 4 archive rows per station; the latest-months row duplicates 03:00 and archive wins.
    assert manifest["smhi"]["rows"] == 6 * 4
    assert (tmp_path / "smhi_air_temperature.csv.gz").exists()
