"""
guardian_api.py
════════════════════════════════════════════════════════════════════════════════
AutoHealX AI — REST API Server  (port 5002)

Endpoints:
  GET  /api/status              → live snapshot of all services
  GET  /api/services            → list all monitored services + health
  GET  /api/services/<name>     → single service detail
  GET  /api/history             → last 50 decision log events
  GET  /api/history/<n>         → last N events
  GET  /api/model/stats         → IsolationForest training info + score dist
  GET  /api/predictions         → current trend predictions (predict_breach)
  GET  /api/metrics             → raw metrics for all services
  POST /api/threshold           → body: {"value": 0.70} — change score threshold
  POST /api/recover/<service>   → manually trigger recovery
  GET  /health                  → API health check

Run standalone:  python guardian_api.py
Or imported by:  guardian.py starts it as a thread

Usage examples:
  curl http://localhost:5002/api/status
  curl http://localhost:5002/api/history/5
  curl -X POST http://localhost:5002/api/threshold -H "Content-Type: application/json" -d '{"value":0.70}'
  curl -X POST http://localhost:5002/api/recover/web
════════════════════════════════════════════════════════════════════════════════
"""

import json
import logging
import os
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

logger = logging.getLogger("guardian.api")

API_PORT    = 5002
API_VERSION = "1.0.0"


# ══════════════════════════════════════════════════════════════════════════════
# State accessor helpers
# ══════════════════════════════════════════════════════════════════════════════

def _get_guardian_state() -> dict:
    """Read live state from guardian.py's shared store."""
    try:
        import guardian
        return guardian.get_state_snapshot()
    except Exception:
        return {}


def _get_threshold() -> float:
    try:
        import guardian
        return guardian.SCORE_THRESHOLD
    except Exception:
        return 0.65


def _set_threshold(val: float) -> bool:
    try:
        import guardian
        guardian.SCORE_THRESHOLD = val
        return True
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Response builders
# ══════════════════════════════════════════════════════════════════════════════

def _json_response(data: dict | list) -> bytes:
    return json.dumps(data, indent=2, default=str).encode("utf-8")


def _ok(data: dict | list) -> tuple:
    return 200, "application/json", _json_response({"ok": True, "data": data})


def _error(code: int, msg: str) -> tuple:
    return code, "application/json", _json_response({"ok": False, "error": msg})


# ══════════════════════════════════════════════════════════════════════════════
# Route handlers
# ══════════════════════════════════════════════════════════════════════════════

def route_health(_params: dict) -> tuple:
    return _ok({
        "status":    "ok",
        "api":       "AutoHealX AI Guardian API",
        "version":   API_VERSION,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    })


def route_status(_params: dict) -> tuple:
    """Full snapshot of current guardian state."""
    state = _get_guardian_state()
    if not state:
        return _error(503, "Guardian offline — start guardian.py first")
    return _ok({
        "timestamp":        datetime.now().isoformat(timespec="seconds"),
        "tick":             state.get("tick"),
        "threshold":        _get_threshold(),
        "baseline_ready":   state.get("baseline_ready"),
        "baseline_samples": state.get("baseline_samples"),
        "score":            state.get("score"),
        "severity":         state.get("severity"),
        "classification":   state.get("classification"),
        "recoveries_total": state.get("recoveries_total"),
        "anomalies_total":  state.get("anomalies_total"),
        "heartbeat_up":     state.get("heartbeat_up"),
        "server_online":    state.get("server_online"),
        "services":         state.get("services", {}),
    })


def route_services(_params: dict) -> tuple:
    """List all monitored services with health status."""
    state    = _get_guardian_state()
    services = state.get("services", {})

    if not services:
        # Single-service fallback
        return _ok({
            "count": 1,
            "services": {
                "default": {
                    "online":   state.get("server_online", False),
                    "severity": state.get("severity", "UNKNOWN"),
                    "score":    state.get("score", 0),
                    "metrics":  state.get("metrics", {}),
                }
            }
        })

    return _ok({
        "count":    len(services),
        "services": {
            name: {
                "online":   svc.get("online", False),
                "severity": svc.get("severity", "UNKNOWN"),
                "score":    svc.get("score", 0),
                "metrics":  svc.get("metrics", {}),
                "action":   svc.get("action"),
            }
            for name, svc in services.items()
        }
    })


def route_service_detail(name: str) -> tuple:
    """Single service full detail."""
    state    = _get_guardian_state()
    services = state.get("services", {})

    if name not in services:
        available = list(services.keys())
        return _error(404, f"Service '{name}' not found. Available: {available}")

    svc = services[name]
    return _ok({
        "name":           name,
        "timestamp":      datetime.now().isoformat(timespec="seconds"),
        "online":         svc.get("online", False),
        "severity":       svc.get("severity", "UNKNOWN"),
        "score":          svc.get("score", 0),
        "classification": svc.get("classification", "UNKNOWN"),
        "metrics":        svc.get("metrics", {}),
        "action":         svc.get("action"),
        "baseline_ready": svc.get("baseline_ready", False),
    })


def route_history(n: int = 50) -> tuple:
    """Last N decision log events."""
    n     = min(max(1, n), 100)
    state = _get_guardian_state()
    events = state.get("events", [])[:n]
    return _ok({
        "count":      len(events),
        "requested":  n,
        "events":     events,
    })


def route_model_stats(_params: dict) -> tuple:
    """IsolationForest training info and score distribution."""
    try:
        import anomaly_detector as ad

        learner = ad.learner
        if not learner.baseline_ready:
            return _ok({
                "baseline_ready":   False,
                "baseline_samples": len(learner.samples),
                "baseline_total":   ad.BASELINE_SIZE,
                "message":          "Model not yet trained — collecting baseline samples",
            })

        import numpy as np
        train_scores = learner.model.score_samples(np.array(learner.samples))

        return _ok({
            "baseline_ready":     True,
            "baseline_samples":   len(learner.samples),
            "model_type":         "IsolationForest",
            "n_estimators":       learner.model.n_estimators,
            "contamination":      learner.model.contamination,
            "score_distribution": {
                "mean":   round(float(learner.score_mu),  4),
                "std":    round(float(learner.score_sig), 4),
                "min":    round(float(train_scores.min()), 4),
                "max":    round(float(train_scores.max()), 4),
            },
            "metric_baselines": {
                k: {"mean": round(v[0], 2), "std": round(v[1], 2)}
                for k, v in learner.metric_stats.items()
            },
            "threshold": _get_threshold(),
        })
    except Exception as exc:
        return _error(500, f"Cannot access model: {exc}")


def route_predictions(_params: dict) -> tuple:
    """Current trend-based breach predictions."""
    try:
        import anomaly_detector as ad

        cpu_hist  = ad.learner.get_history("cpu")
        mem_hist  = ad.learner.get_history("mem")
        disk_hist = ad.learner.get_history("disk")
        net_hist  = ad.learner.get_history("net")

        predictions = ad.predict_breach(cpu_hist, mem_hist, disk_hist, net_hist)

        return _ok({
            "timestamp":   datetime.now().isoformat(timespec="seconds"),
            "count":       len(predictions),
            "predictions": predictions,
            "message":     (
                "No breaches predicted — all metrics stable"
                if not predictions
                else f"{len(predictions)} metric(s) trending toward threshold"
            ),
        })
    except Exception as exc:
        return _error(500, f"Prediction error: {exc}")


def route_metrics(_params: dict) -> tuple:
    """Raw live metrics for all services."""
    state    = _get_guardian_state()
    services = state.get("services", {})

    if services:
        return _ok({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "services": {
                name: svc.get("metrics", {})
                for name, svc in services.items()
            }
        })
    else:
        return _ok({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "metrics":   state.get("metrics", {}),
        })


def route_set_threshold(body: bytes) -> tuple:
    """POST /api/threshold — change score threshold live."""
    try:
        data = json.loads(body.decode("utf-8"))
        val  = float(data.get("value", -1))
        if not 0.1 <= val <= 0.95:
            return _error(400, "Value must be between 0.10 and 0.95")
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        return _error(400, f"Invalid JSON body: {exc}")

    old = _get_threshold()
    if _set_threshold(val):
        return _ok({
            "old_threshold": old,
            "new_threshold": val,
            "message": f"Threshold updated: {old:.2f} → {val:.2f}",
        })
    else:
        return _error(503, "Guardian not running — cannot update threshold")


def route_manual_recover(service_name: str) -> tuple:
    """POST /api/recover/<service> — manually trigger recovery."""
    container_map = {
        "web":      "autohealx-web",
        "api":      "autohealx-api",
        "database": "autohealx-database",
        "cache":    "autohealx-cache",
        "default":  "ai-server",
    }

    if service_name not in container_map:
        return _error(404,
            f"Unknown service '{service_name}'. "
            f"Valid: {list(container_map.keys())}")

    try:
        import recovery_engine
        original = recovery_engine.CONTAINER_NAME
        recovery_engine.CONTAINER_NAME = container_map[service_name]
        result = recovery_engine.restart_container(classification="MANUAL_API")
        recovery_engine.CONTAINER_NAME = original

        return _ok({
            "service":   service_name,
            "container": container_map[service_name],
            "result":    result,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        })
    except Exception as exc:
        return _error(500, f"Recovery failed: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# HTTP Request Handler
# ══════════════════════════════════════════════════════════════════════════════

class APIHandler(BaseHTTPRequestHandler):
    """Route HTTP requests to the correct handler function."""

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _send(self, status: int, content_type: str, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type",   content_type)
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path   = self.path.split("?")[0].rstrip("/")
        parts  = [p for p in path.split("/") if p]

        # ── Route table ──────────────────────────────────────────────────────
        if path in ("/health", "/api/health"):
            self._send(*route_health({}))

        elif path in ("/api/status", "/api"):
            self._send(*route_status({}))

        elif path == "/api/services":
            self._send(*route_services({}))

        elif len(parts) == 3 and parts[0] == "api" and parts[1] == "services":
            self._send(*route_service_detail(parts[2]))

        elif path == "/api/history":
            self._send(*route_history(50))

        elif len(parts) == 3 and parts[0] == "api" and parts[1] == "history":
            try:
                n = int(parts[2])
            except ValueError:
                n = 10
            self._send(*route_history(n))

        elif path == "/api/model/stats":
            self._send(*route_model_stats({}))

        elif path == "/api/predictions":
            self._send(*route_predictions({}))

        elif path == "/api/metrics":
            self._send(*route_metrics({}))

        else:
            self._send(*_error(404, f"Endpoint not found: {path}"))

    def do_POST(self):
        path   = self.path.split("?")[0].rstrip("/")
        parts  = [p for p in path.split("/") if p]

        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length) if length else b"{}"

        if path == "/api/threshold":
            self._send(*route_set_threshold(body))

        elif len(parts) == 3 and parts[0] == "api" and parts[1] == "recover":
            self._send(*route_manual_recover(parts[2]))

        else:
            self._send(*_error(404, f"POST endpoint not found: {path}"))

    def log_message(self, fmt, *args):
        # Log to guardian logger instead of stderr
        logger.debug("[API] %s %s", self.address_string(), fmt % args)


# ══════════════════════════════════════════════════════════════════════════════
# Server lifecycle
# ══════════════════════════════════════════════════════════════════════════════

def start_api_thread(port: int = API_PORT) -> threading.Thread:
    """Start the API server in a daemon thread. Called by guardian.py."""
    server = HTTPServer(("0.0.0.0", port), APIHandler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="GuardianAPI",
        daemon=True,
    )
    thread.start()
    logger.info("[API] Guardian REST API started on http://localhost:%d", port)
    return thread


if __name__ == "__main__":
    # Standalone mode — useful for testing endpoints without full guardian
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-8s | %(message)s")
    print(f"\n  AutoHealX AI — Guardian REST API")
    print(f"  http://localhost:{API_PORT}/api/status")
    print(f"  http://localhost:{API_PORT}/api/history")
    print(f"  http://localhost:{API_PORT}/api/model/stats")
    print(f"  http://localhost:{API_PORT}/api/predictions")
    print(f"\n  Ctrl-C to stop\n")

    server = HTTPServer(("0.0.0.0", API_PORT), APIHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  API server stopped.")