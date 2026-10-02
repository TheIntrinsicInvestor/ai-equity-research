import importlib
import os
import sys


def test_secret_key_never_the_known_default(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    sys.modules.pop("config", None)
    import config
    importlib.reload(config)
    assert config.SECRET_KEY != "equity-research-dev"
    assert len(config.SECRET_KEY) >= 32


def test_secret_key_respects_env(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    sys.modules.pop("config", None)
    import config
    importlib.reload(config)
    assert config.SECRET_KEY == "x" * 40
