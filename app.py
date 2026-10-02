from flask import Flask, render_template, request, redirect, url_for, session
from pipeline.orchestrator import run_phase1, run_phase2
from config import SECRET_KEY

app = Flask(__name__)
app.secret_key = SECRET_KEY

# In-memory store for pipeline data between the two request phases.
# Safe for single-user local use.
_store: dict = {}


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    ticker = request.form.get("ticker", "").strip().upper()
    if not ticker:
        return redirect(url_for("index"))

    result = run_phase1(ticker)
    if result.get("error"):
        return render_template("index.html", error=result["error"])

    if len(_store) > 25:
        _store.clear()
    _store[ticker] = result["target_data"]
    session["ticker"] = ticker
    session["suggested_peers"] = result["suggested_peers"]
    session["peer_warning"] = result.get("peer_warning")
    return redirect(url_for("peers"))


@app.route("/peers", methods=["GET"])
def peers():
    ticker = session.get("ticker")
    suggested = session.get("suggested_peers", [])
    peer_warning = session.get("peer_warning")
    if not ticker:
        return redirect(url_for("index"))
    return render_template("peers.html", ticker=ticker,
                           suggested_peers=suggested, peer_warning=peer_warning)


@app.route("/generate", methods=["POST"])
def generate():
    ticker = session.get("ticker")
    target_data = _store.get(ticker)
    if not ticker or target_data is None:
        return redirect(url_for("index"))

    confirmed_peers = request.form.getlist("peers")
    extra_peers = [
        t.strip().upper()
        for t in request.form.get("extra_peers", "").split(",")
        if t.strip()
    ]
    all_peers = list(dict.fromkeys(confirmed_peers + extra_peers))

    try:
        report_html = run_phase2(ticker, target_data, all_peers)
        return report_html
    except Exception as exc:
        import traceback
        error_detail = traceback.format_exc()
        return render_template("index.html", error=f"Report generation failed: {exc}\n\n{error_detail}"), 500


if __name__ == "__main__":
    import os
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=5000)
