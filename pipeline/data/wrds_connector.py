import threading
import time
import wrds
from config import WRDS_USERNAME, WRDS_PASSWORD

_conn = None
_lock = threading.Lock()
_keepalive_started = False

_KEEPALIVE_INTERVAL = 120  # seconds


def _keepalive_loop():
    while True:
        time.sleep(_KEEPALIVE_INTERVAL)
        global _conn
        with _lock:
            if _conn is not None:
                try:
                    _conn.raw_sql("SELECT 1")
                except Exception:
                    pass  # connection dead — will be recreated on next get_connection()


def _start_keepalive():
    global _keepalive_started
    if not _keepalive_started:
        t = threading.Thread(target=_keepalive_loop, daemon=True)
        t.start()
        _keepalive_started = True


def get_connection() -> wrds.Connection:
    global _conn
    if not WRDS_USERNAME or not WRDS_PASSWORD:
        raise RuntimeError(
            "WRDS_USERNAME and WRDS_PASSWORD environment variables must be set."
        )
    with _lock:
        if _conn is None:
            _conn = wrds.Connection(wrds_username=WRDS_USERNAME, wrds_password=WRDS_PASSWORD)
            _start_keepalive()
    return _conn
