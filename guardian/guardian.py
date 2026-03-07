"""
guardian.py
════════════════════════════════════════════════════════════════════════════════
Stage 3 — AI Guardian: Main Entry Point

What this file does:
  1. Sets up guardian.log (rotating file handler) + colored console output
  2. Starts the heartbeat monitor daemon thread
  3. Polls GET http://localhost:5000/metrics every 5 seconds
  4. Passes metrics to anomaly_detector → gets score + classification
  5. If score > 0.65 AND baseline is ready → calls recovery_engine
  6. Logs every tick to guardian.log with timestamp, metrics, score, action

Colored terminal output:
  GREEN  → NORMAL / score < 0.40
  YELLOW → WARNING / score 0.40–0.65
  RED    → CRITICAL / score > 0.65 + any recovery action

Usage:
  pip install -r requirements_guardian.txt
  python guardian.py

  # Optional flags:
  python guardian.py --url http://192.168.1.10:5000   # remote server
  python guardian.py --interval 3                      # poll every 3s
  python guardian.py --no-color                        # disable ANSI colors
════════════════════════════════════════════════════════════════════════════════
"""

import argparse
import logging
import logging.handlers
import os
import sys
import time
from datetime import datetime

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from colorama import Fore, Style, init as colorama_init

import anomaly_detector
import heartbeat_monitor
import recovery_engine

# Optional integrations — imported lazily so guardian works without them
try:
    import notifier
    _NOTIFIER_AVAILABLE = True
except ImportError:
    _NOTIFIER_AVAILABLE = False

try:
    import telegram_bot
    _TELEGRAM_AVAILABLE = True
except ImportError:
    _TELEGRAM_AVAILABLE = False

try:
    import guardian_api
    _API_AVAILABLE = True
except ImportError:
    _API_AVAILABLE = False

# ── Colorama init ─────────────────────────────────────────────────────────────
# strip=False: keep ANSI codes on Windows terminals that support them
# autoreset=True: reset color after each print automatically
colorama_init(autoreset=True, strip=False)

# ── Constants ─────────────────────────────────────────────────────────────────
DEFAULT_SERVER_URL    = "http://localhost:5000"
DEFAULT_POLL_INTERVAL = 5          # seconds
SCORE_THRESHOLD       = 0.65       # above this → recovery fires
LOG_FILE              = "guardian.log"
LOG_MAX_BYTES         = 10 * 1024 * 1024   # 10 MB per log file
LOG_BACKUP_COUNT      = 3                   # keep 3 rotated files

# ── Separator widths (terminal display) ──────────────────────────────────────
WIDE  = 72
THIN  = 48
UI_API_PORT   = 5001     # guardian state API — browser polls this for live data
REST_API_PORT = 5002     # full REST API — curl / Postman / frontend

# ── Multi-service cluster config ──────────────────────────────────────────────
# Maps service name → (url, container_name)
# Add or remove services here to match your docker-compose.yml
SERVICES = {
    "web":      ("http://localhost:5000", "autohealx-web"),
    "api":      ("http://localhost:5010", "autohealx-api"),
    "database": ("http://localhost:5020", "autohealx-database"),
    "cache":    ("http://localhost:5030", "autohealx-cache"),
}
# Set to True for multi-service mode, False for original single-service mode
MULTI_SERVICE_MODE = True


# ══════════════════════════════════════════════════════════════════════════════
# Live State Store
# Shared dict written by the main loop, read by the API thread.
# Protected by a lock so reads always get a consistent snapshot.
# ══════════════════════════════════════════════════════════════════════════════

_state_lock = threading.Lock()
_live_state: dict = {
    "tick":             0,
    "timestamp":        None,
    "baseline_ready":   False,
    "baseline_samples": 0,
    "baseline_total":   anomaly_detector.BASELINE_SIZE,
    "score":            0.0,
    "severity":         "LEARNING",
    "classification":   "LEARNING",
    "action":           None,
    "metrics": {
        "cpu":   0.0,
        "mem":   0.0,
        "disk":  0.0,
        "net":   0.0,
        "procs": 0,
    },
    "heartbeat_up":    True,
    "heartbeat_fails": 0,
    "recoveries_total": 0,
    "anomalies_total":  0,
    "events":           [],   # last 50 events for the UI log
    "server_online":    False,
    "services":         {},   # populated in multi-service mode
    "predictions":      [],   # predictive breach warnings
}
_recoveries_total = 0
_anomalies_total  = 0


def get_all_services_snapshot() -> dict:
    """Return the services sub-dict (used by telegram_bot and guardian_api)."""
    with _state_lock:
        import copy
        return copy.deepcopy(_live_state.get("services", {}))


def update_state(**kwargs) -> None:
    """Thread-safe update of the live state dict."""
    with _state_lock:
        _live_state.update(kwargs)


def get_state_snapshot() -> dict:
    """Return a deep copy of the current state."""
    with _state_lock:
        import copy
        return copy.deepcopy(_live_state)


def push_event(event_type: str, score: float, action: str, severity: str) -> None:
    """Prepend an event to the events list (max 50 entries)."""
    with _state_lock:
        _live_state["events"].insert(0, {
            "ts":         datetime.now().strftime("%H:%M:%S"),
            "type":       event_type,
            "score":      round(score, 3),
            "action":     action,
            "severity":   severity,
        })
        if len(_live_state["events"]) > 50:
            _live_state["events"].pop()


# ══════════════════════════════════════════════════════════════════════════════
# Minimal HTTP API server (runs in daemon thread on port 5001)
# Serves:
#   GET /state  →  JSON snapshot of _live_state  (polled by guardian_ui.html)
#   OPTIONS /*  →  CORS preflight response
# ══════════════════════════════════════════════════════════════════════════════

class _StateHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler — serves /state as JSON with CORS headers."""

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path == "/state":
            payload = json.dumps(get_state_snapshot()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type",   "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass   # silence default request logging — guardian.log is noisy enough


def start_state_api(port: int = UI_API_PORT) -> threading.Thread:
    """Start the state API server in a daemon thread."""
    server = HTTPServer(("0.0.0.0", port), _StateHandler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="GuardianStateAPI",
        daemon=True,
    )
    thread.start()
    return thread


# ══════════════════════════════════════════════════════════════════════════════
# Logging setup
# ══════════════════════════════════════════════════════════════════════════════

def setup_logging(log_file: str = LOG_FILE) -> logging.Logger:
    """
    Configure two handlers:
      1. RotatingFileHandler  → guardian.log  (plain text, no ANSI codes)
      2. StreamHandler        → stdout        (ANSI colors added by ColorFormatter)

    Returns the root 'guardian' logger.
    """
    logger = logging.getLogger("guardian")
    logger.setLevel(logging.DEBUG)

    # ── File handler — plain text, all levels ────────────────────────────────
    file_fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_file,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_fmt)

    # ── Console handler — INFO and above (DEBUG too verbose for terminal) ────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s"))   # raw; color added by print calls

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


# ══════════════════════════════════════════════════════════════════════════════
# Color helpers
# ══════════════════════════════════════════════════════════════════════════════

def _color_for_score(score: float, use_color: bool) -> str:
    """Return the colorama Fore color string for a given score."""
    if not use_color:
        return ""
    if score > SCORE_THRESHOLD:
        return Fore.RED
    if score > 0.40:
        return Fore.YELLOW
    return Fore.GREEN


def _severity_label(score: float) -> str:
    if score > SCORE_THRESHOLD:
        return "CRITICAL"
    if score > 0.40:
        return "WARNING"
    return "NORMAL"


def cprint(msg: str, color: str = "", use_color: bool = True) -> None:
    """Print with optional ANSI color."""
    if use_color and color:
        print(f"{color}{msg}{Style.RESET_ALL}")
    else:
        print(msg)


# ══════════════════════════════════════════════════════════════════════════════
# Metrics fetch
# ══════════════════════════════════════════════════════════════════════════════

def fetch_metrics(server_url: str) -> dict | None:
    """
    GET /metrics from the Docker Flask server.
    Returns normalised dict or None on failure.

    Expected server JSON:
      { cpu_percent, memory_percent, disk_percent,
        net_bytes_sent, net_bytes_recv, active_processes }
    """
    try:
        resp = requests.get(
            f"{server_url}/metrics",
            timeout=3,
        )
        resp.raise_for_status()
        raw = resp.json()
        return {
            "cpu":   float(raw.get("cpu_percent",    0)),
            "mem":   float(raw.get("memory_percent", 0)),
            "disk":  float(raw.get("disk_percent",   0)),
            # net: sum bytes_sent + bytes_recv, convert to KB/s
            "net":   (float(raw.get("net_bytes_sent", 0))
                    + float(raw.get("net_bytes_recv", 0))) / 1024.0,
            "procs": int(raw.get("active_processes", 0)),
        }
    except requests.exceptions.ConnectionError:
        return None
    except requests.exceptions.Timeout:
        return None
    except Exception as exc:
        logging.getLogger("guardian.main").warning("fetch_metrics error: %s", exc)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Display helpers (colored terminal blocks)
# ══════════════════════════════════════════════════════════════════════════════

def print_tick_header(tick: int, ts: str, color: str, use_color: bool) -> None:
    """Print the ── TICK N ── separator line."""
    label = f"── TICK {tick:>4}  {ts} "
    line  = label + "─" * max(0, WIDE - len(label))
    cprint(line, color, use_color)


def print_metrics_block(
    metrics: dict,
    score: float,
    classification: str,
    color: str,
    use_color: bool,
) -> None:
    """Print the formatted metrics + score block."""
    sev = _severity_label(score)
    cprint(
        f"  CPU  {metrics['cpu']:>6.1f}%   "
        f"MEM  {metrics['mem']:>6.1f}%   "
        f"DISK {metrics['disk']:>6.1f}%   "
        f"NET  {metrics['net']:>7.1f} KB/s   "
        f"PROCS {metrics['procs']:>4}",
        color, use_color,
    )
    cprint(
        f"  SCORE {score:.3f}   SEVERITY {sev:<8}   CLASS {classification}",
        color, use_color,
    )


def print_action_line(action_result: str, color: str, use_color: bool) -> None:
    """Print the recovery action result line."""
    cprint(f"  ACTION → {action_result}", color, use_color)


def print_baseline_progress(n: int, use_color: bool) -> None:
    """Print baseline learning status in dim yellow."""
    msg = f"  ⏳ Learning baseline... ({n}/{anomaly_detector.BASELINE_SIZE})"
    cprint(msg, Fore.YELLOW, use_color)


def print_offline_warning(tick: int, use_color: bool) -> None:
    cprint(
        f"  ✗ SERVER OFFLINE — tick {tick}, retrying in {DEFAULT_POLL_INTERVAL}s",
        Fore.RED, use_color,
    )


def print_heartbeat_status(use_color: bool) -> None:
    """Print a compact heartbeat status line (bottom of each tick)."""
    hb = heartbeat_monitor.get_status()
    fails = hb.get("consecutive_fails", 0)
    up    = hb.get("up", False)
    hb_color = Fore.GREEN if up else Fore.RED
    status_str = "UP" if up else f"DOWN (fails={fails})"
    cprint(f"  HB {status_str}", hb_color if use_color else "", use_color)


# ══════════════════════════════════════════════════════════════════════════════
# Guardian log entry (structured for guardian.log)
# ══════════════════════════════════════════════════════════════════════════════

def log_tick(
    logger: logging.Logger,
    tick: int,
    metrics: dict | None,
    score: float,
    classification: str,
    action_taken: str,
    baseline_ready: bool,
) -> None:
    """
    Write a structured tick entry to guardian.log.
    Format:
      TICK=N | SCORE=0.XXX | SEV=NORMAL | CLASS=CPU_OVERLOAD |
      CPU=XX.X% MEM=XX.X% DISK=XX.X% NET=XX.X KB/s PROCS=N |
      ACTION=<action or NONE> | BASELINE=READY/LEARNING
    """
    if metrics is None:
        logger.warning(
            "TICK=%04d | SERVER_OFFLINE | no metrics available", tick
        )
        return

    logger.info(
        "TICK=%04d | SCORE=%.3f | SEV=%-8s | CLASS=%-20s | "
        "CPU=%5.1f%% MEM=%5.1f%% DISK=%5.1f%% NET=%7.1f KB/s PROCS=%d | "
        "ACTION=%-35s | BASELINE=%s",
        tick,
        score,
        _severity_label(score),
        classification,
        metrics["cpu"],
        metrics["mem"],
        metrics["disk"],
        metrics["net"],
        metrics["procs"],
        action_taken if action_taken else "NONE",
        "READY" if baseline_ready else f"LEARNING({len(anomaly_detector.learner.samples)}/{anomaly_detector.BASELINE_SIZE})",
    )


# ══════════════════════════════════════════════════════════════════════════════
# Main guardian loop
# ══════════════════════════════════════════════════════════════════════════════

def run_guardian(server_url: str, poll_interval: int, use_color: bool) -> None:
    """
    Main polling loop. Runs until Ctrl-C.

    Per-tick flow:
      1. fetch_metrics()
      2. anomaly_detector.learner.add_sample() (always, builds baseline)
      3. anomaly_detector.calc_anomaly_score()
      4. anomaly_detector.classify_anomaly()
      5. If score > 0.65 AND baseline_ready → recovery_engine.select_and_execute()
      6. log_tick() to guardian.log
      7. sleep(poll_interval)
    """
    logger = logging.getLogger("guardian.main")
    tick   = 0

    logger.info("=" * WIDE)
    logger.info("AI GUARDIAN STARTED")
    logger.info("  server      : %s", server_url)
    logger.info("  poll        : %ds", poll_interval)
    logger.info("  threshold   : %.2f", SCORE_THRESHOLD)
    logger.info("  baseline    : %d samples", anomaly_detector.BASELINE_SIZE)
    logger.info("  log file    : %s", os.path.abspath(LOG_FILE))
    logger.info("=" * WIDE)

    cprint("=" * WIDE, Fore.CYAN, use_color)
    cprint("  AI GUARDIAN MONITOR  —  Stage 3", Fore.CYAN, use_color)
    cprint(f"  server={server_url}  poll={poll_interval}s  threshold={SCORE_THRESHOLD}", Fore.CYAN, use_color)
    cprint("  Press Ctrl-C to stop.", Fore.CYAN, use_color)
    cprint("=" * WIDE, Fore.CYAN, use_color)
    print()

    while True:
        tick += 1
        now  = datetime.now()
        ts   = now.strftime("%H:%M:%S")

        # ── 1. Fetch ─────────────────────────────────────────────────────────
        metrics = fetch_metrics(server_url)

        if metrics is None:
            color = Fore.RED if use_color else ""
            print_tick_header(tick, ts, color, use_color)
            print_offline_warning(tick, use_color)
            print_heartbeat_status(use_color)
            log_tick(logger, tick, None, 0.0, "SERVER_OFFLINE", "NONE", False)
            update_state(tick=tick, timestamp=ts, server_online=False,
                         severity="OFFLINE", classification="SERVER_OFFLINE")
            push_event("SERVER_OFFLINE", 0.0, "— connection lost", "critical")
            time.sleep(poll_interval)
            continue

        cpu  = metrics["cpu"]
        mem  = metrics["mem"]
        disk = metrics["disk"]
        net  = metrics["net"]

        # ── 2. Feed baseline learner ─────────────────────────────────────────
        anomaly_detector.learner.add_sample(cpu, mem, disk, net)
        baseline_ready = anomaly_detector.learner.baseline_ready

        # ── 3. Score ─────────────────────────────────────────────────────────
        score = anomaly_detector.calc_anomaly_score(cpu, mem, disk, net)

        # ── 4. Classify ──────────────────────────────────────────────────────
        classification = anomaly_detector.classify_anomaly(cpu, mem, disk, net)

        # ── 5. Recovery ──────────────────────────────────────────────────────
        action_taken = None

        if baseline_ready and score > SCORE_THRESHOLD:
            logger.warning(
                "TICK=%04d — ANOMALY: score=%.3f > %.2f  class=%s — triggering recovery",
                tick, score, SCORE_THRESHOLD, classification
            )
            try:
                # In multi-service mode, set the container name before recovery
                if MULTI_SERVICE_MODE:
                    recovery_engine.CONTAINER_NAME = SERVICES.get(
                        server_url, (server_url, "ai-server")
                    )[1] if isinstance(SERVICES.get(server_url), tuple) else "ai-server"
                action_taken = recovery_engine.select_and_execute(classification, score)
            except Exception as exc:
                action_taken = f"RECOVERY_ERROR: {exc}"
                logger.error("Recovery exception at tick %d: %s", tick, exc, exc_info=True)

        # ── 5b. Notifications (Slack + Telegram) ─────────────────────────────
        service_label = getattr(run_guardian, "_current_service", "web")

        if baseline_ready and score > SCORE_THRESHOLD and _NOTIFIER_AVAILABLE:
            notifier.notify_anomaly(
                service        = service_label,
                classification = classification,
                score          = score,
                metrics        = {
                    "cpu": cpu, "mem": mem, "disk": disk, "net": net
                },
                action_taken   = action_taken,
            )
            if action_taken:
                notifier.notify_recovery(service_label, classification, action_taken)

        # ── 5c. Predictive alerts ─────────────────────────────────────────────
        predictions = []
        if baseline_ready:
            predictions = anomaly_detector.predict_breach(
                anomaly_detector.learner.get_history("cpu"),
                anomaly_detector.learner.get_history("mem"),
                anomaly_detector.learner.get_history("disk"),
                anomaly_detector.learner.get_history("net"),
            )
            for pred in predictions:
                logger.warning(
                    "[PREDICT] %s trending to threshold in ~%ds (current=%.1f slope=%.4f)",
                    pred["metric"], pred["eta_s"], pred["current"], pred["slope"]
                )
                cprint(
                    f"  🔮 PREDICTED: {pred['metric'].upper()} breach in ~{pred['eta_s']}s "
                    f"(current={pred['current']:.1f})",
                    Fore.YELLOW if use_color else "", use_color
                )
                if _NOTIFIER_AVAILABLE:
                    notifier.notify_prediction(
                        service_label, pred["metric"],
                        pred["eta_s"], pred["current"]
                    )

        # ── 6. Update live state (read by guardian_ui.html via /state API) ────
        global _recoveries_total, _anomalies_total
        hb = heartbeat_monitor.get_status()
        sev = _severity_label(score)
        if action_taken:
            _recoveries_total += 1
        if score > SCORE_THRESHOLD and baseline_ready:
            _anomalies_total += 1

        update_state(
            tick             = tick,
            timestamp        = ts,
            baseline_ready   = baseline_ready,
            baseline_samples = len(anomaly_detector.learner.samples),
            score            = round(score, 3),
            severity         = sev if baseline_ready else "LEARNING",
            classification   = classification if baseline_ready else "LEARNING",
            action           = action_taken,
            metrics          = {
                "cpu":   round(cpu,  1),
                "mem":   round(mem,  1),
                "disk":  round(disk, 1),
                "net":   round(net,  1),
                "procs": metrics.get("procs", 0),
            },
            heartbeat_up     = hb.get("up", True),
            heartbeat_fails  = hb.get("consecutive_fails", 0),
            recoveries_total = _recoveries_total,
            anomalies_total  = _anomalies_total,
            server_online    = True,
            predictions      = predictions,
        )
        # Push to event log when interesting
        if action_taken:
            push_event(classification, score, action_taken, "critical")
        elif score > 0.4 and baseline_ready:
            push_event(classification, score,
                       anomaly_detector.classify_anomaly(cpu, mem, disk, net), "warning")
        elif tick % 10 == 0 and baseline_ready:
            push_event("HEARTBEAT", score, "— all systems nominal", "normal")

        # ── 7. Terminal output ────────────────────────────────────────────────
        color = _color_for_score(score, use_color)
        print_tick_header(tick, ts, color, use_color)

        if not baseline_ready:
            print_baseline_progress(len(anomaly_detector.learner.samples), use_color)

        print_metrics_block(metrics, score, classification, color, use_color)

        if action_taken:
            print_action_line(action_taken, Fore.RED if use_color else "", use_color)

        print_heartbeat_status(use_color)

        # ── 7. File log ───────────────────────────────────────────────────────
        log_tick(
            logger, tick, metrics, score, classification,
            action_taken, baseline_ready,
        )

        # ── 8. Sleep ─────────────────────────────────────────────────────────
        time.sleep(poll_interval)

# ══════════════════════════════════════════════════════════════════════════════
# Multi-Service Guardian Loop
# ══════════════════════════════════════════════════════════════════════════════

def run_guardian_multi(poll_interval: int, use_color: bool) -> None:
    """
    Multi-service main loop. Monitors all services defined in SERVICES dict.
    """
    from anomaly_detector import BaselineLearner, calc_anomaly_score, classify_anomaly
    from anomaly_detector import predict_breach, BASELINE_SIZE
    import anomaly_detector as ad_module

    logger = logging.getLogger("guardian.multi")

    # Per-service learner instances (independent baselines)
    learners: dict = {name: BaselineLearner() for name in SERVICES}

    tick = 0

    cprint("=" * WIDE, Fore.CYAN, use_color)
    cprint(f"  AutoHealX AI — MULTI-SERVICE MODE ({len(SERVICES)} services)",
           Fore.CYAN, use_color)
    for name, (url, container) in SERVICES.items():
        cprint(f"    {name:<10} → {url}  [{container}]", Fore.CYAN, use_color)
    cprint("=" * WIDE, Fore.CYAN, use_color)
    print()

    while True:
        tick += 1
        ts    = datetime.now().strftime("%H:%M:%S")
        services_state = {}
        global _recoveries_total, _anomalies_total

        cprint(f"\n── TICK {tick:>4}  {ts}  [MULTI-SERVICE] " +
               "─" * max(0, WIDE - 26 - len(str(tick))), Fore.CYAN, use_color)

        for svc_name, (svc_url, container_name) in SERVICES.items():
            metrics = fetch_metrics(svc_url)
            learner = learners[svc_name]

            if metrics is None:
                cprint(f"  [{svc_name:<10}] ✗ OFFLINE", Fore.RED, use_color)
                services_state[svc_name] = {
                    "online":         False,
                    "severity":       "OFFLINE",
                    "score":          0.0,
                    "classification": "SERVER_OFFLINE",
                    "action":         None,
                    "metrics":        {"cpu": 0, "mem": 0, "disk": 0, "net": 0, "procs": 0},
                    "baseline_ready": learner.baseline_ready,
                    "baseline_samples": len(learner.samples),
                    "predictions":    [],
                }
                continue

            cpu, mem, disk, net = (
                metrics["cpu"], metrics["mem"],
                metrics["disk"], metrics["net"]
            )

            # Feed this service's learner
            learner.add_sample(cpu, mem, disk, net)
            baseline_ready = learner.baseline_ready

            # Swap module-level learner temporarily for scoring
            original_learner    = ad_module.learner
            ad_module.learner   = learner
            score          = calc_anomaly_score(cpu, mem, disk, net)
            classification = classify_anomaly(cpu, mem, disk, net)

            # Predictions for this service
            preds = predict_breach(
                learner.get_history("cpu"), learner.get_history("mem"),
                learner.get_history("disk"), learner.get_history("net"),
            ) if baseline_ready else []

            ad_module.learner = original_learner   # restore

            # Recovery — only fire if classification is NOT NORMAL
            action_taken = None
            if baseline_ready and score > SCORE_THRESHOLD and classification != "NORMAL":
                original_container = recovery_engine.CONTAINER_NAME
                recovery_engine.CONTAINER_NAME = container_name
                try:
                    action_taken = recovery_engine.select_and_execute(
                        classification, score
                    )
                    # Only count as recovery if actual action taken
                    if action_taken and "NO_ACTION" not in action_taken:
                        _recoveries_total += 1
                except Exception as exc:
                    action_taken = f"RECOVERY_ERROR: {exc}"
                finally:
                    recovery_engine.CONTAINER_NAME = original_container

                _anomalies_total += 1

                # Notify
                run_guardian._current_service = svc_name
                if _NOTIFIER_AVAILABLE:
                    notifier.notify_anomaly(
                        svc_name, classification, score,
                        {"cpu": cpu, "mem": mem, "disk": disk, "net": net},
                        action_taken
                    )
                    if action_taken and "NO_ACTION" not in action_taken:
                        notifier.notify_recovery(svc_name, classification, action_taken)

            # Terminal line
            sev    = _severity_label(score)
            color  = _color_for_score(score, use_color)
            bl_str = f"({len(learner.samples)}/{BASELINE_SIZE})" if not baseline_ready else ""
            action_str = f"  → {action_taken[:50]}" if action_taken else ""

            cprint(
                f"  [{svc_name:<10}] CPU {cpu:5.1f}%  MEM {mem:5.1f}%  "
                f"SCORE {score:.3f}  {sev:<8} {bl_str}{action_str}",
                color, use_color
            )

            for pred in preds:
                cprint(
                    f"  {'':>12}🔮 PREDICTED {pred['metric'].upper()} "
                    f"breach in ~{pred['eta_s']}s",
                    Fore.YELLOW if use_color else "", use_color
                )

            # Push to event log
            if action_taken and "NO_ACTION" not in action_taken:
                push_event(f"{svc_name}:{classification}", score, action_taken, "critical")
            elif score > 0.4 and baseline_ready:
                push_event(f"{svc_name}:{classification}", score, "— monitoring", "warning")

            services_state[svc_name] = {
                "online":           True,
                "severity":         sev if baseline_ready else "LEARNING",
                "score":            round(score, 3),
                "classification":   classification if baseline_ready else "LEARNING",
                "action":           action_taken if (action_taken and "NO_ACTION" not in action_taken) else None,
                "metrics": {
                    "cpu":   round(cpu,  1),
                    "mem":   round(mem,  1),
                    "disk":  round(disk, 1),
                    "net":   round(net,  1),
                    "procs": metrics.get("procs", 0),
                },
                "baseline_ready":   baseline_ready,
                "baseline_samples": len(learner.samples) if not baseline_ready else BASELINE_SIZE,
                "predictions":      preds,
            }

        # ── Aggregate state for top-level (used by Telegram bot + UI) ──────
        online_svcs = [s for s in services_state.values() if s["online"]]

        # Worst score across all services
        worst_score = max((s["score"] for s in online_svcs), default=0.0)
        worst_sev   = _severity_label(worst_score)

        # Aggregate baseline: ready only when ALL services are ready
        all_baselines_ready = (
            all(s.get("baseline_ready", False) for s in online_svcs)
            if online_svcs else False
        )

        # Min baseline samples across services (for progress display)
        min_baseline_samples = min(
            (s.get("baseline_samples", 0) for s in online_svcs),
            default=0
        )

        # Pick the worst service's metrics for top-level display
        worst_svc = max(online_svcs, key=lambda s: s["score"]) if online_svcs else None
        worst_metrics = worst_svc["metrics"] if worst_svc else {
            "cpu": 0, "mem": 0, "disk": 0, "net": 0, "procs": 0
        }

        # Pick latest real action (not NO_ACTION) from any service
        latest_action = None
        for svc_data in services_state.values():
            a = svc_data.get("action")
            if a and "NO_ACTION" not in a:
                latest_action = a
                break

        hb = heartbeat_monitor.get_status()

        update_state(
            tick              = tick,
            timestamp         = ts,
            baseline_ready    = all_baselines_ready,
            baseline_samples  = min_baseline_samples,
            baseline_total    = BASELINE_SIZE,
            score             = worst_score,
            severity          = worst_sev if all_baselines_ready else "LEARNING",
            classification    = "MULTI_SERVICE",
            action            = latest_action,
            metrics           = worst_metrics,
            server_online     = bool(online_svcs),
            recoveries_total  = _recoveries_total,
            anomalies_total   = _anomalies_total,
            heartbeat_up      = hb.get("up", True),
            heartbeat_fails   = hb.get("consecutive_fails", 0),
            services          = services_state,
        )

        # Periodic heartbeat event
        if tick % 10 == 0 and all_baselines_ready:
            push_event("HEARTBEAT", worst_score, "— all systems nominal", "normal")

        time.sleep(poll_interval)


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI Guardian — Stage 3 self-healing monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python guardian.py
  python guardian.py --url http://192.168.1.10:5000
  python guardian.py --interval 3
  python guardian.py --no-color
        """,
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_SERVER_URL,
        metavar="URL",
        help=f"Flask server base URL (default: {DEFAULT_SERVER_URL})",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL,
        metavar="SECONDS",
        help=f"Poll interval in seconds (default: {DEFAULT_POLL_INTERVAL})",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        dest="no_color",
        help="Disable ANSI color output (useful for piping to a file)",
    )
    return parser.parse_args()


def main() -> None:
    args      = parse_args()
    use_color = not args.no_color

    # Set up logging before anything else
    logger = setup_logging(LOG_FILE)
    root   = logging.getLogger("guardian")
    root.info("guardian.py starting up — pid=%d", os.getpid())

    # Patch heartbeat_monitor to use the same server URL as the main loop
    heartbeat_monitor.HEALTH_ENDPOINT = f"{args.url}/health"

    # Start heartbeat daemon thread
    hb_thread = heartbeat_monitor.start_heartbeat_thread()
    root.info("Heartbeat thread running — tid=%d", hb_thread.ident)

    # Start Guardian State API (serves /state on port 5001 for guardian_ui.html)
    api_thread = start_state_api(UI_API_PORT)
    root.info("Guardian State API running on http://localhost:%d/state", UI_API_PORT)
    cprint(f"  Guardian UI  → open guardian_ui.html  (state API  :{UI_API_PORT})",
           Fore.CYAN, use_color)

    # Start REST API on port 5002
    if _API_AVAILABLE:
        rest_thread = guardian_api.start_api_thread(REST_API_PORT)
        root.info("REST API running on http://localhost:%d", REST_API_PORT)
        cprint(f"  REST API     → http://localhost:{REST_API_PORT}/api/status",
               Fore.CYAN, use_color)
    else:
        cprint("  REST API     → guardian_api.py not found — skipping", Fore.YELLOW, use_color)

    # Start Telegram bot
    if _TELEGRAM_AVAILABLE:
        tg_thread = telegram_bot.start_bot_thread()
        root.info("Telegram bot started — tid=%d", tg_thread.ident)
        cprint("  Telegram Bot → running — type /help in your chat",
               Fore.CYAN, use_color)
    else:
        cprint("  Telegram Bot → telegram_bot.py not found — skipping", Fore.YELLOW, use_color)

    cprint("", Fore.CYAN, use_color)

    # Run the main guardian loop (blocks until Ctrl-C)
    try:
        if MULTI_SERVICE_MODE:
            run_guardian_multi(poll_interval=args.interval, use_color=use_color)
        else:
            run_guardian(
                server_url    = args.url,
                poll_interval = args.interval,
                use_color     = use_color,
            )
    except KeyboardInterrupt:
        print()
        cprint("\n  Guardian stopped by user (Ctrl-C).", Fore.CYAN, use_color)
        root.info("Guardian stopped by KeyboardInterrupt.")
        sys.exit(0)
    except Exception as exc:
        root.critical("Fatal error in guardian loop: %s", exc, exc_info=True)
        cprint(f"\n  FATAL: {exc}", Fore.RED, use_color)
        sys.exit(1)


if __name__ == "__main__":
    main()