"""
recovery_engine.py
════════════════════════════════════════════════════════════════════════════════
Stage 3 — AI Guardian: Recovery Engine

Responsibilities:
  • Connect to the local Docker daemon
  • Execute targeted recovery actions against CONTAINER_NAME
  • Log BEFORE and AFTER every action with result status
  • select_and_execute() routes classification → correct recovery function
  • Thread-safe: actions guarded by a lock (heartbeat + main loop share this)

Recovery actions:
  1. restart_container()       → docker restart <name>
  2. kill_heavy_process()      → exec into container, find + kill top CPU pid
  3. activate_rate_limiting()  → sets in-memory flag, logs advisory
  4. cleanup_logs()            → exec into container, remove /tmp/*.log /tmp/*.tmp
  5. auto_restart_on_crash()   → same as restart_container, triggered by heartbeat
  6. activate_safe_mode()      → stops non-essential processes, logs SAFE MODE
════════════════════════════════════════════════════════════════════════════════
"""

import logging
import threading
import time
from datetime import datetime

import docker
import docker.errors

# ── Logger (writes to guardian.log and stdout) ────────────────────────────────
logger = logging.getLogger("guardian.recovery")

# ── Docker config ────────────────────────────────────────────────────────────
# Default to the primary web service container; guardian overrides this in
# multi-service mode and for manual API/Telegram recoveries.
CONTAINER_NAME = "autohealx-web"

# ── Shared state ─────────────────────────────────────────────────────────────
_action_lock       = threading.Lock()   # prevents concurrent docker ops
_rate_limit_active = False              # set by activate_rate_limiting()
_safe_mode_active  = False              # set by activate_safe_mode()

# Tracks last action taken (classification → action_name, timestamp)
last_action: dict = {
    "classification": None,
    "action":         None,
    "result":         None,
    "timestamp":      None,
}


# ══════════════════════════════════════════════════════════════════════════════
# Docker client helper
# ══════════════════════════════════════════════════════════════════════════════

def _get_client() -> docker.DockerClient:
    """
    Returns a connected DockerClient.
    Raises RuntimeError with a helpful message if Docker is not reachable.
    """
    try:
        client = docker.from_env()
        client.ping()   # verify connectivity before any operation
        return client
    except docker.errors.DockerException as exc:
        raise RuntimeError(
            f"Cannot connect to Docker daemon: {exc}\n"
            "Make sure Docker Desktop / dockerd is running."
        ) from exc


def _get_container(client: docker.DockerClient):
    """
    Returns the container object for CONTAINER_NAME.
    Raises RuntimeError if container not found.
    """
    try:
        return client.containers.get(CONTAINER_NAME)
    except docker.errors.NotFound:
        raise RuntimeError(
            f"Container '{CONTAINER_NAME}' not found. "
            "Is docker-compose up running?"
        )


def _record_action(classification: str, action: str, result: str) -> None:
    """Update shared last_action dict and emit a structured log line."""
    last_action.update({
        "classification": classification,
        "action":         action,
        "result":         result,
        "timestamp":      datetime.now().isoformat(timespec="seconds"),
    })
    logger.info(
        "[RECOVERY] action=%-30s  class=%-20s  result=%s",
        action, classification, result
    )


# ══════════════════════════════════════════════════════════════════════════════
# Action 1 — restart_container
# ══════════════════════════════════════════════════════════════════════════════

def restart_container(classification: str = "MANUAL") -> str:
    """
    Gracefully restart the Docker container (SIGTERM → wait → SIGKILL).
    Timeout: 10 seconds before force-kill.
    Returns result string.
    """
    with _action_lock:
        logger.warning("[RECOVERY] BEFORE restart_container — container=%s", CONTAINER_NAME)
        try:
            client    = _get_client()
            container = _get_container(client)

            # Log current container status
            container.reload()
            before_status = container.status
            logger.warning("[RECOVERY]   container status before: %s", before_status)

            # Restart with 10s timeout
            container.restart(timeout=10)

            # Brief wait so status has time to update
            time.sleep(2)
            container.reload()
            after_status = container.status

            result = f"OK — status changed {before_status} → {after_status}"
            logger.info("[RECOVERY] AFTER  restart_container — %s", result)
            _record_action(classification, "restart_container", result)
            return result

        except RuntimeError as exc:
            result = f"FAILED — {exc}"
            logger.error("[RECOVERY] restart_container failed: %s", result)
            _record_action(classification, "restart_container", result)
            return result


# ══════════════════════════════════════════════════════════════════════════════
# Action 2 — kill_heavy_process
# ══════════════════════════════════════════════════════════════════════════════

def kill_heavy_process(classification: str = "CPU_OVERLOAD") -> str:
    """
    Exec into the container and kill the top CPU-consuming process.
    Uses 'ps aux --sort=-%cpu' and sends SIGKILL to the top PID.
    Skips PID 1 (init) to avoid crashing the container.
    Returns result string.
    """
    with _action_lock:
        logger.warning("[RECOVERY] BEFORE kill_heavy_process — container=%s", CONTAINER_NAME)
        try:
            client    = _get_client()
            container = _get_container(client)

            # Step 1: find top CPU pid (skip PID 1 — that's the container entrypoint)
            find_cmd = (
                "ps aux --sort=-%cpu "
                "| awk 'NR>1 && $2!=1 {print $2; exit}'"
            )
            exit_code, output = container.exec_run(
                cmd=["sh", "-c", find_cmd],
                demux=False,
            )
            raw_pid = output.decode("utf-8", errors="replace").strip() if output else ""

            if not raw_pid or not raw_pid.isdigit():
                result = "SKIPPED — no killable process found (output: " + repr(raw_pid) + ")"
                logger.warning("[RECOVERY]   %s", result)
                _record_action(classification, "kill_heavy_process", result)
                return result

            pid = int(raw_pid)
            logger.warning("[RECOVERY]   top CPU pid inside container: %d — sending SIGKILL", pid)

            # Step 2: kill the process
            kill_code, kill_out = container.exec_run(
                cmd=["kill", "-9", str(pid)],
            )
            kill_output = kill_out.decode("utf-8", errors="replace").strip() if kill_out else ""

            if kill_code == 0:
                result = f"OK — killed PID {pid} inside {CONTAINER_NAME}"
            else:
                result = f"PARTIAL — kill exit={kill_code} output={kill_output!r}"

            logger.info("[RECOVERY] AFTER  kill_heavy_process — %s", result)
            _record_action(classification, "kill_heavy_process", result)
            return result

        except RuntimeError as exc:
            result = f"FAILED — {exc}"
            logger.error("[RECOVERY] kill_heavy_process failed: %s", result)
            _record_action(classification, "kill_heavy_process", result)
            return result


# ══════════════════════════════════════════════════════════════════════════════
# Action 3 — activate_rate_limiting
# ══════════════════════════════════════════════════════════════════════════════

def activate_rate_limiting(classification: str = "TRAFFIC_SPIKE") -> str:
    """
    Sets the in-memory rate-limiting flag and logs an advisory.
    In a real system this would push a config change to nginx/HAProxy.
    In the hackathon demo it clearly signals the decision in the log.
    Returns result string.
    """
    global _rate_limit_active
    with _action_lock:
        logger.warning("[RECOVERY] BEFORE activate_rate_limiting — flag was: %s", _rate_limit_active)

        _rate_limit_active = True

        result = "OK — rate limiting ACTIVATED (flag set; in production: push nginx config)"
        logger.warning("[RECOVERY] AFTER  activate_rate_limiting — %s", result)
        logger.warning("[RECOVERY] *** RATE LIMIT ACTIVE — incoming requests being throttled ***")

        _record_action(classification, "activate_rate_limiting", result)
        return result


def deactivate_rate_limiting() -> None:
    """Reset the rate-limiting flag once traffic returns to normal."""
    global _rate_limit_active
    _rate_limit_active = False
    logger.info("[RECOVERY] rate limiting deactivated — traffic normalised")


def is_rate_limit_active() -> bool:
    return _rate_limit_active


# ══════════════════════════════════════════════════════════════════════════════
# Action 4 — cleanup_logs
# ══════════════════════════════════════════════════════════════════════════════

def cleanup_logs(classification: str = "DISK_PRESSURE") -> str:
    """
    Exec into the container and delete temporary log / scratch files
    under /tmp to free disk space.
    Returns result string including bytes freed (if available).
    """
    with _action_lock:
        logger.warning("[RECOVERY] BEFORE cleanup_logs — container=%s", CONTAINER_NAME)
        try:
            client    = _get_client()
            container = _get_container(client)

            # Check /tmp disk usage before cleanup
            du_code, du_out = container.exec_run(
                cmd=["sh", "-c", "du -sh /tmp 2>/dev/null || echo 'unknown'"],
            )
            before_size = du_out.decode("utf-8", errors="replace").strip() if du_out else "unknown"
            logger.warning("[RECOVERY]   /tmp usage before: %s", before_size)

            # Remove log, tmp, and large dummy files written by disk_flood chaos
            cleanup_cmd = (
                "find /tmp -maxdepth 1 "
                r"\\( -name '*.log' -o -name '*.tmp' -o -name 'dummy_*' \\) "
                "-type f -delete 2>/dev/null; "
                "echo done"
            )
            exit_code, output = container.exec_run(
                cmd=["sh", "-c", cleanup_cmd],
            )
            cleanup_output = output.decode("utf-8", errors="replace").strip() if output else ""

            # Check /tmp after cleanup
            du_code2, du_out2 = container.exec_run(
                cmd=["sh", "-c", "du -sh /tmp 2>/dev/null || echo 'unknown'"],
            )
            after_size = du_out2.decode("utf-8", errors="replace").strip() if du_out2 else "unknown"

            result = f"OK — /tmp cleaned ({before_size} → {after_size}), exit={exit_code}"
            logger.info("[RECOVERY] AFTER  cleanup_logs — %s", result)
            _record_action(classification, "cleanup_logs", result)
            return result

        except RuntimeError as exc:
            result = f"FAILED — {exc}"
            logger.error("[RECOVERY] cleanup_logs failed: %s", result)
            _record_action(classification, "cleanup_logs", result)
            return result


# ══════════════════════════════════════════════════════════════════════════════
# Action 5 — auto_restart_on_crash
# ══════════════════════════════════════════════════════════════════════════════

def auto_restart_on_crash(classification: str = "PROCESS_CRASH") -> str:
    """
    Triggered by heartbeat_monitor when consecutive_fails >= 3.
    Functionally identical to restart_container() but with its own log marker
    so the guardian.log clearly shows it was a heartbeat-triggered restart
    rather than a metric-triggered restart.
    Returns result string.
    """
    logger.critical(
        "[RECOVERY] *** AUTO RESTART ON CRASH — heartbeat lost, forcing restart ***"
    )
    result = restart_container(classification=classification)
    # Add crash-restart marker on top of the generic restart log
    logger.critical("[RECOVERY] *** AUTO RESTART COMPLETE — result: %s ***", result)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Action 6 — activate_safe_mode
# ══════════════════════════════════════════════════════════════════════════════

def activate_safe_mode(classification: str = "ANOMALY_DETECTED") -> str:
    """
    Enters safe mode:
      1. Sets the in-memory safe-mode flag
      2. Kills all non-essential processes inside the container
         (keeps PID 1 and the Flask/gunicorn process)
      3. Logs SAFE MODE ACTIVE clearly in the log
    Returns result string.
    """
    global _safe_mode_active
    with _action_lock:
        logger.warning("[RECOVERY] BEFORE activate_safe_mode — was: %s", _safe_mode_active)

        _safe_mode_active = True

        # Try to kill non-essential background processes inside the container.
        # We kill processes that are NOT PID 1 and NOT the main Python process.
        try:
            client    = _get_client()
            container = _get_container(client)

            # Kill all stress/dummy processes if they're running (from chaos)
            # Pattern: any 'stress', 'dd', 'yes' commands that might still be up
            kill_cmd = (
                "for p in stress dd yes; do "
                "  pids=$(pgrep -x $p 2>/dev/null); "
                "  if [ -n \"$pids\" ]; then kill -9 $pids 2>/dev/null; fi; "
                "done; "
                "echo 'non-essential processes killed'"
            )
            exit_code, output = container.exec_run(cmd=["sh", "-c", kill_cmd])
            kill_result = output.decode("utf-8", errors="replace").strip() if output else ""
            docker_result = f"exec exit={exit_code} msg={kill_result!r}"
        except RuntimeError as exc:
            docker_result = f"docker unavailable ({exc})"

        result = f"OK — SAFE MODE ACTIVATED, {docker_result}"
        logger.warning("[RECOVERY] AFTER  activate_safe_mode — %s", result)
        logger.warning("[RECOVERY] *** SAFE MODE ACTIVE — non-essential processes stopped ***")

        _record_action(classification, "activate_safe_mode", result)
        return result


def deactivate_safe_mode() -> None:
    """Reset safe mode once metrics return to normal."""
    global _safe_mode_active
    _safe_mode_active = False
    logger.info("[RECOVERY] safe mode deactivated — system stabilised")


def is_safe_mode_active() -> bool:
    return _safe_mode_active


# ══════════════════════════════════════════════════════════════════════════════
# Router — select_and_execute
# ══════════════════════════════════════════════════════════════════════════════

def select_and_execute(classification: str, score: float) -> str:
    """
    Routes a classification label to the correct recovery action.

    Decision table:
      PROCESS_CRASH    → auto_restart_on_crash    (highest priority)
      CPU_OVERLOAD     → kill_heavy_process
      MEMORY_LEAK      → restart_container
      DISK_PRESSURE    → cleanup_logs
      TRAFFIC_SPIKE    → activate_rate_limiting
      ANOMALY_DETECTED → activate_safe_mode       (catch-all for unknown anomalies)
      NORMAL           → (no action, returns early)

    The score is logged for audit but does not alter the routing —
    guardian.py already filters on score > 0.65 before calling here.
    """
    logger.warning(
        "[RECOVERY] select_and_execute called — classification=%s  score=%.3f",
        classification, score
    )

    if classification == "PROCESS_CRASH":
        return auto_restart_on_crash(classification)

    elif classification == "CPU_OVERLOAD":
        return kill_heavy_process(classification)

    elif classification == "MEMORY_LEAK":
        return restart_container(classification)

    elif classification == "DISK_PRESSURE":
        return cleanup_logs(classification)

    elif classification == "TRAFFIC_SPIKE":
        return activate_rate_limiting(classification)

    elif classification == "ANOMALY_DETECTED":
        return activate_safe_mode(classification)

    else:
        # NORMAL or unrecognised — do nothing
        logger.debug(
            "[RECOVERY] No action for classification=%s (score=%.3f)",
            classification, score
        )
        return f"NO_ACTION — classification={classification}"