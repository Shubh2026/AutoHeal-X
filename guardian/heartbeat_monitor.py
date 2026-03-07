"""
heartbeat_monitor.py
════════════════════════════════════════════════════════════════════════════════
Stage 3 — AI Guardian: Heartbeat Monitor

Runs as a daemon thread alongside the main guardian loop.
Independently pings GET /health every 3 seconds.
Tracks consecutive failures and fires auto_restart_on_crash() after 3 fails.

Thread model:
  • Started by guardian.py as threading.Thread(daemon=True)
  • Will be killed automatically when the main process exits
  • Does NOT need a shutdown event — daemon status handles cleanup

State accessible from guardian.py:
  • get_status()  → dict with current heartbeat stats
  • is_server_up() → bool
════════════════════════════════════════════════════════════════════════════════
"""

import logging
import threading
import time
from datetime import datetime

import requests

# ── Logger ────────────────────────────────────────────────────────────────────
logger = logging.getLogger("guardian.heartbeat")

# ── Config ────────────────────────────────────────────────────────────────────
HEALTH_ENDPOINT     = "http://localhost:5000/health"
PING_INTERVAL_S     = 3          # seconds between pings
REQUEST_TIMEOUT_S   = 2          # HTTP timeout per ping
FAIL_THRESHOLD      = 3          # consecutive fails before triggering restart
RESTART_COOLDOWN_S  = 30         # seconds to wait before allowing another restart

# ── Shared state (read-only from outside this module) ─────────────────────────
_state_lock = threading.Lock()

_state = {
    "up":                True,    # last known server status
    "consecutive_fails": 0,       # current run of failures
    "total_pings":       0,       # all-time ping count
    "total_failures":    0,       # all-time failure count
    "last_ping_ts":      None,    # ISO timestamp of last ping
    "last_fail_ts":      None,    # ISO timestamp of last failure
    "last_recovery_ts":  None,    # ISO timestamp of last triggered recovery
    "uptime_start":      None,    # timestamp of last confirmed online moment
}


# ══════════════════════════════════════════════════════════════════════════════
# Public accessors (thread-safe)
# ══════════════════════════════════════════════════════════════════════════════

def get_status() -> dict:
    """Return a snapshot of current heartbeat state (copy, not reference)."""
    with _state_lock:
        return dict(_state)


def is_server_up() -> bool:
    """Quick check: is the server currently considered online?"""
    with _state_lock:
        return _state["up"]


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

def _ping_once() -> bool:
    """
    Send a single GET /health request.
    Returns True if the server responded with HTTP 200.
    Logs the response or error at appropriate log levels.
    """
    try:
        resp = requests.get(HEALTH_ENDPOINT, timeout=REQUEST_TIMEOUT_S)
        if resp.status_code == 200:
            data = resp.json()
            uptime = data.get("uptime_seconds", "?")
            logger.debug(
                "[HB] ✓ ONLINE — status=%s  uptime=%ss",
                data.get("status", "?"), uptime
            )
            return True
        else:
            logger.warning("[HB] ✗ BAD STATUS — HTTP %d from /health", resp.status_code)
            return False

    except requests.exceptions.Timeout:
        logger.warning("[HB] ✗ TIMEOUT — /health did not respond within %ds", REQUEST_TIMEOUT_S)
        return False

    except requests.exceptions.ConnectionError:
        logger.warning("[HB] ✗ CONNECTION REFUSED — server may be down or restarting")
        return False

    except Exception as exc:
        logger.warning("[HB] ✗ UNEXPECTED — %s: %s", type(exc).__name__, exc)
        return False


def _update_state(success: bool, now_ts: str) -> int:
    """
    Update the shared state dict based on the ping result.
    Returns current consecutive_fails count (after update).
    Thread-safe.
    """
    with _state_lock:
        _state["total_pings"] += 1
        _state["last_ping_ts"] = now_ts

        if success:
            _state["up"]                = True
            _state["consecutive_fails"] = 0
            _state["uptime_start"]      = _state["uptime_start"] or now_ts
        else:
            _state["up"]                 = False
            _state["consecutive_fails"] += 1
            _state["total_failures"]    += 1
            _state["last_fail_ts"]       = now_ts
            _state["uptime_start"]       = None   # reset uptime tracking

        return _state["consecutive_fails"]


def _can_trigger_recovery() -> bool:
    """
    Returns True if enough time has passed since the last recovery attempt.
    Prevents hammering the Docker daemon with rapid restarts.
    """
    with _state_lock:
        last = _state["last_recovery_ts"]
    if last is None:
        return True
    elapsed = (datetime.now() - datetime.fromisoformat(last)).total_seconds()
    return elapsed >= RESTART_COOLDOWN_S


def _record_recovery_attempt(now_ts: str) -> None:
    with _state_lock:
        _state["last_recovery_ts"] = now_ts


# ══════════════════════════════════════════════════════════════════════════════
# Main loop — run in daemon thread
# ══════════════════════════════════════════════════════════════════════════════

def run_heartbeat_loop() -> None:
    """
    Infinite loop — ping /health every PING_INTERVAL_S seconds.

    Logic:
      • ping succeeds  → reset consecutive_fails, log at DEBUG
      • ping fails     → increment counter, log at WARNING
      • fails >= 3     → trigger auto_restart_on_crash() if cooldown passed
      • after restart  → reset consecutive_fails to avoid retriggering immediately
    """
    # Import here (not at module level) to avoid circular import at startup.
    # guardian.py imports heartbeat_monitor; recovery_engine has no back-reference.
    import recovery_engine

    logger.info(
        "[HB] Heartbeat monitor started — pinging %s every %ds  (fail threshold: %d)",
        HEALTH_ENDPOINT, PING_INTERVAL_S, FAIL_THRESHOLD
    )

    while True:
        now_ts  = datetime.now().isoformat(timespec="seconds")
        success = _ping_once()
        fails   = _update_state(success, now_ts)

        if not success:
            logger.warning(
                "[HB] consecutive_fails=%d / %d  (threshold: %d)",
                fails, FAIL_THRESHOLD, FAIL_THRESHOLD
            )

        # ── Trigger recovery ─────────────────────────────────────────────────
        if fails >= FAIL_THRESHOLD:
            if _can_trigger_recovery():
                logger.critical(
                    "[HB] *** THRESHOLD REACHED — %d consecutive failures ***",
                    fails
                )
                logger.critical(
                    "[HB] Calling auto_restart_on_crash() at %s", now_ts
                )
                _record_recovery_attempt(now_ts)
                try:
                    result = recovery_engine.auto_restart_on_crash("PROCESS_CRASH")
                    logger.info("[HB] Restart result: %s", result)
                except Exception as exc:
                    logger.error(
                        "[HB] Recovery failed — %s: %s",
                        type(exc).__name__, exc
                    )

                # Reset consecutive_fails so we don't immediately re-trigger
                with _state_lock:
                    _state["consecutive_fails"] = 0

            else:
                logger.warning(
                    "[HB] Recovery suppressed — cooldown active (%ds since last attempt)",
                    RESTART_COOLDOWN_S
                )

        time.sleep(PING_INTERVAL_S)


# ══════════════════════════════════════════════════════════════════════════════
# Thread factory
# ══════════════════════════════════════════════════════════════════════════════

def start_heartbeat_thread() -> threading.Thread:
    """
    Create, start, and return the daemon heartbeat thread.
    Caller (guardian.py) just calls this once at startup and can ignore the
    returned Thread object — the daemon flag means it auto-dies with the process.
    """
    thread = threading.Thread(
        target=run_heartbeat_loop,
        name="HeartbeatMonitor",
        daemon=True,        # killed automatically when main process exits
    )
    thread.start()
    logger.info("[HB] Heartbeat thread started — tid=%d", thread.ident)
    return thread