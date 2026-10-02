"""Two interleaved users must not receive each other's target_data (review 1.4)."""
import app as app_module


def test_interleaved_analyze_does_not_clobber(monkeypatch):
    calls = []

    def fake_phase1(ticker):
        return {"target_data": {"for": ticker}, "suggested_peers": [], "error": None}

    def fake_phase2(ticker, target_data, peers):
        calls.append((ticker, target_data))
        return f"<html>{ticker}</html>"

    monkeypatch.setattr(app_module, "run_phase1", fake_phase1)
    monkeypatch.setattr(app_module, "run_phase2", fake_phase2)
    client_a = app_module.app.test_client()
    client_b = app_module.app.test_client()

    client_a.post("/analyze", data={"ticker": "AAPL"})
    client_b.post("/analyze", data={"ticker": "MSFT"})   # would clobber a global store
    client_a.post("/generate", data={"peers": []})

    assert calls[-1] == ("AAPL", {"for": "AAPL"})
