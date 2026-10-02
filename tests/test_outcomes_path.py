import importlib
import sys


def test_outcomes_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("OUTCOMES_DIR", str(tmp_path))
    sys.modules.pop("pipeline.orchestrator", None)
    orch = importlib.import_module("pipeline.orchestrator")
    importlib.reload(orch)
    assert str(tmp_path) in orch._OUTCOMES_PATH
