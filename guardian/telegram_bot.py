"""
telegram_bot.py
═══════════════════════════════════════════════════════════════════════════════
AutoHealX AI — Telegram Command Bot

Commands:
  /status              → live metrics + anomaly score for all services
  /status web          → metrics for a specific service
  /history             → last 10 events from the decision log
  /predict             → current trend analysis
  /recover <service>   → manually trigger recovery for a service
  /threshold <value>   → change the anomaly threshold
  /help                → list all commands
═══════════════════════════════════════════════════════════════════════════════
"""

import logging
import os
import threading
import time
from datetime import datetime

import requests as http_requests

logger = logging.getLogger("guardian.telegram_bot")

# ── Config ──────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
POLL_INTERVAL_S    = 2
REQUEST_TIMEOUT_S  = 10

# ── Guardian API URL (reads live state reliably) ────
GUARDIAN_STATE_URL  = "http://localhost:5001/state"
GUARDIAN_REST_URL   = "http://localhost:5002"


# ═══════════════════════════════════════════════════════════════════════════════
# Telegram API helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"


def send_message(chat_id: str, text: str, parse_mode: str = "Markdown") -> bool:
    if not TELEGRAM_BOT_TOKEN:
        return False
    try:
        resp = http_requests.post(
            _api_url("sendMessage"),
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=REQUEST_TIMEOUT_S,
        )
        return resp.json().get("ok", False)
    except Exception as exc:
        logger.warning("[TG BOT] send_message error: %s", exc)
        return False


def get_updates(offset: int) -> list:
    if not TELEGRAM_BOT_TOKEN:
        return []
    try:
        resp = http_requests.get(
            _api_url("getUpdates"),
            params={"offset": offset, "timeout": 5},
            timeout=REQUEST_TIMEOUT_S,
        )
        data = resp.json()
        return data.get("result", []) if data.get("ok") else []
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# State accessor — reads from Guardian's HTTP API (port 5001)
# This avoids the __main__ vs module import problem entirely.
# ═══════════════════════════════════════════════════════════════════════════════

def _get_state() -> dict:
    """
    Fetch live state from Guardian's HTTP API at localhost:5001/state.
    This is the same endpoint the Guardian UI polls.
    """
    try:
        resp = http_requests.get(GUARDIAN_STATE_URL, timeout=3)
        if resp.status_code == 200:
            return resp.json()
        return {}
    except Exception as exc:
        logger.debug("[TG BOT] Cannot reach guardian state API: %s", exc)
        return {}


def _get_history() -> list:
    state = _get_state()
    return state.get("events", [])


# ═══════════════════════════════════════════════════════════════════════════════
# Command handlers
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_help(chat_id: str, _args: list) -> None:
    text = (
        "🤖 *AutoHealX AI — Command Reference*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📊 *Monitoring*\n"
        "`/status`          — all services live metrics\n"
        "`/status <name>`   — single service (web/api/database/cache)\n"
        "`/predict`         — trend analysis & predictions\n\n"
        "📋 *History*\n"
        "`/history`         — last 10 events\n"
        "`/history <n>`     — last N events (max 20)\n\n"
        "🔧 *Control*\n"
        "`/recover <name>`  — trigger recovery for a service\n"
        "`/threshold <val>` — change score threshold (e.g. 0.70)\n\n"
        "ℹ️ *Info*\n"
        "`/help`            — this message\n"
    )
    send_message(chat_id, text)


def cmd_status(chat_id: str, args: list) -> None:
    """Show live metrics for one or all services."""
    state = _get_state()

    if not state:
        send_message(chat_id,
            "⚠️ *Guardian offline*\nRun `python guardian.py` to start the system.")
        return

    # Check for multi-service data
    services = state.get("services", {})

    # Single service requested
    if args:
        service_name = args[0].lower()
        if service_name not in services:
            available = ", ".join(services.keys()) or "none"
            send_message(chat_id,
                f"❌ Service `{service_name}` not found.\nAvailable: {available}")
            return
        svc = services[service_name]
        _send_service_status(chat_id, service_name, svc)
        return

    # All services overview
    if services:
        _send_multi_status(chat_id, state, services)
    else:
        _send_single_status(chat_id, state)


def _send_multi_status(chat_id: str, state: dict, services: dict) -> None:
    """Send status for all services in multi-service mode."""
    tick = state.get("tick", "?")
    ts   = state.get("timestamp", "?")

    lines = [
        f"📊 *AutoHealX AI — Cluster Status*",
        f"🕐 `{ts}` | Tick `{tick}`",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    for name, svc in services.items():
        score   = svc.get("score", 0)
        sev     = svc.get("severity", "?")
        online  = svc.get("online", False)
        m       = svc.get("metrics", {})
        action  = svc.get("action")
        bl      = svc.get("baseline_ready", False)

        sev_icon = "🔴" if sev == "CRITICAL" else "🟡" if sev == "WARNING" else "🟢"
        status   = "🟢" if online else "🔴 OFFLINE"

        bl_str = "✅" if bl else f"⏳ Learning"

        lines.append(
            f"\n{status} *{name.upper()}* {sev_icon}\n"
            f"Score: `{score:.3f}` | {bl_str}\n"
            f"CPU: `{m.get('cpu',0):.1f}%` | "
            f"MEM: `{m.get('mem',0):.1f}%` | "
            f"DISK: `{m.get('disk',0):.1f}%`\n"
            f"NET: `{m.get('net',0):.0f} KB/s` | "
            f"PROCS: `{m.get('procs',0)}`"
        )
        if action:
            lines.append(f"⚡ Action: `{action[:60]}`")

    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(
        f"🔧 Recoveries: `{state.get('recoveries_total',0)}` | "
        f"⚠️ Anomalies: `{state.get('anomalies_total',0)}`"
    )
    send_message(chat_id, "\n".join(lines))


def _send_service_status(chat_id: str, name: str, svc: dict) -> None:
    score   = svc.get("score", 0)
    sev     = svc.get("severity", "UNKNOWN")
    clf     = svc.get("classification", "—")
    m       = svc.get("metrics", {})
    action  = svc.get("action") or "None"
    online  = svc.get("online", False)
    bl      = svc.get("baseline_ready", False)

    sev_icon = "🔴" if sev == "CRITICAL" else "🟡" if sev == "WARNING" else "🟢"
    status   = "🟢 ONLINE" if online else "🔴 OFFLINE"
    bl_str   = "✅ Ready" if bl else "⏳ Learning"

    text = (
        f"📊 *{name.upper()}* — Live Status\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Status: {status}\n"
        f"Severity: {sev_icon} `{sev}`\n"
        f"Score: `{score:.3f}` / 1.000\n"
        f"Classification: `{clf}`\n"
        f"Baseline: {bl_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"CPU:  `{m.get('cpu',0):.1f}%`\n"
        f"MEM:  `{m.get('mem',0):.1f}%`\n"
        f"DISK: `{m.get('disk',0):.1f}%`\n"
        f"NET:  `{m.get('net',0):.0f} KB/s`\n"
        f"PROCS: `{m.get('procs',0)}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Last action: `{action}`"
    )
    send_message(chat_id, text)


def _send_single_status(chat_id: str, state: dict) -> None:
    """Fallback for single-service mode."""
    score  = state.get("score", 0)
    sev    = state.get("severity", "UNKNOWN")
    clf    = state.get("classification", "—")
    m      = state.get("metrics", {})
    tick   = state.get("tick", "?")
    ts     = state.get("timestamp", "—")
    bl     = state.get("baseline_ready", False)

    sev_icon = "🔴" if sev == "CRITICAL" else "🟡" if sev == "WARNING" else "🟢"
    bl_str   = "✅ Ready" if bl else f"⏳ Learning ({state.get('baseline_samples',0)}/50)"

    text = (
        f"📊 *AutoHealX AI — Status*\n"
        f"🕐 `{ts}` | Tick `{tick}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Severity: {sev_icon} `{sev}`\n"
        f"Score: `{score:.3f}` / 1.000\n"
        f"Class: `{clf}`\n"
        f"Baseline: {bl_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"CPU:  `{m.get('cpu',0):.1f}%`\n"
        f"MEM:  `{m.get('mem',0):.1f}%`\n"
        f"DISK: `{m.get('disk',0):.1f}%`\n"
        f"NET:  `{m.get('net',0):.0f} KB/s`\n"
        f"PROCS: `{m.get('procs',0)}`"
    )
    send_message(chat_id, text)


def cmd_history(chat_id: str, args: list) -> None:
    try:
        n = min(int(args[0]), 20) if args else 10
    except (ValueError, IndexError):
        n = 10

    events = _get_history()
    if not events:
        send_message(chat_id, "📋 No events recorded yet.")
        return

    lines = [f"📋 *AutoHealX AI — Last {min(n, len(events))} Events*\n"]
    for e in events[:n]:
        sev = e.get("severity", "normal")
        icon = "🔴" if sev == "critical" else "🟡" if sev == "warning" else "🟢"
        lines.append(
            f"{icon} `{e.get('ts','?')}` — *{e.get('type','?')}*\n"
            f"   Score: `{e.get('score',0):.3f}` | {e.get('action','—')}\n"
        )

    send_message(chat_id, "\n".join(lines))


def cmd_predict(chat_id: str, _args: list) -> None:
    """Show trend predictions from live state."""
    state = _get_state()

    if not state:
        send_message(chat_id, "⚠️ Guardian offline — cannot predict.")
        return

    services = state.get("services", {})
    lines = ["🔮 *AutoHealX AI — Trend Predictions*\n"]

    has_predictions = False

    for svc_name, svc_data in services.items():
        preds = svc_data.get("predictions", [])
        if preds:
            has_predictions = True
            lines.append(f"⚠️ *{svc_name.upper()}* — Predicted breaches:")
            for pred in preds:
                lines.append(
                    f"  `{pred['metric'].upper()}` — current `{pred['current']:.1f}` → "
                    f"threshold `{pred['threshold']:.1f}` in ~`{pred['eta_s']}s`"
                )
        else:
            bl = svc_data.get("baseline_ready", False)
            if bl:
                lines.append(f"✅ *{svc_name.upper()}*: All metrics stable")
            else:
                lines.append(f"⏳ *{svc_name.upper()}*: Learning baseline...")
        lines.append("")

    if not services:
        preds = state.get("predictions", [])
        if preds:
            for pred in preds:
                lines.append(
                    f"⚠️ `{pred['metric'].upper()}` — breach in ~`{pred['eta_s']}s`"
                )
        else:
            lines.append("✅ All metrics stable — no breach predicted")

    send_message(chat_id, "\n".join(lines))


def cmd_recover(chat_id: str, args: list) -> None:
    if not args:
        send_message(chat_id,
            "Usage: `/recover <service>`\n"
            "Services: `web`, `api`, `database`, `cache`")
        return

    service_name = args[0].lower()
    valid = {"web", "api", "database", "cache"}
    if service_name not in valid:
        send_message(chat_id,
            f"❌ Unknown service `{service_name}`\n"
            f"Valid: {', '.join(f'`{s}`' for s in sorted(valid))}")
        return

    send_message(chat_id,
        f"🔧 *Manual recovery triggered for `{service_name}`*\n"
        f"Restarting container `autohealx-{service_name}`...")

    try:
        import recovery_engine
        container_map = {
            "web":      "autohealx-web",
            "api":      "autohealx-api",
            "database": "autohealx-database",
            "cache":    "autohealx-cache",
        }
        original = recovery_engine.CONTAINER_NAME
        recovery_engine.CONTAINER_NAME = container_map[service_name]
        result = recovery_engine.restart_container(classification="MANUAL_TELEGRAM")
        recovery_engine.CONTAINER_NAME = original

        send_message(chat_id,
            f"✅ *Recovery complete*\n"
            f"Service: `{service_name}`\n"
            f"Result: `{result[:120]}`")
    except Exception as exc:
        send_message(chat_id, f"❌ Recovery failed: `{exc}`")


def cmd_threshold(chat_id: str, args: list) -> None:
    if not args:
        send_message(chat_id,
            "Usage: `/threshold <value>`\nExample: `/threshold 0.70`")
        return
    try:
        val = float(args[0])
        if not 0.1 <= val <= 0.95:
            raise ValueError("out of range")
    except ValueError:
        send_message(chat_id, "❌ Invalid value. Use a number between 0.10 and 0.95")
        return

    try:
        import guardian
        old = guardian.SCORE_THRESHOLD
        guardian.SCORE_THRESHOLD = val
        send_message(chat_id,
            f"✅ *Threshold updated*\n"
            f"Old: `{old:.2f}` → New: `{val:.2f}`\n"
            f"Recovery will now fire when score > `{val:.2f}`")
    except Exception as exc:
        send_message(chat_id, f"❌ Failed to update threshold: `{exc}`")


# ═══════════════════════════════════════════════════════════════════════════════
# Proactive Alert Sender (called by guardian.py when anomaly detected)
# ═══════════════════════════════════════════════════════════════════════════════

def send_alert(service: str, classification: str, score: float,
               metrics: dict, action: str = None) -> bool:
    """
    Send a proactive anomaly alert to Telegram.
    Called by guardian.py main loop or notifier.py.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    m = metrics or {}
    action_line = f"⚡ Action: `{action}`" if action else "🔍 Monitoring..."

    text = (
        f"🚨 *ANOMALY ALERT — {service.upper()}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Classification: `{classification}`\n"
        f"Score: `{score:.3f}` / 1.000\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"CPU:  `{m.get('cpu',0):.1f}%`\n"
        f"MEM:  `{m.get('mem',0):.1f}%`\n"
        f"DISK: `{m.get('disk',0):.1f}%`\n"
        f"NET:  `{m.get('net',0):.0f} KB/s`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{action_line}"
    )
    return send_message(TELEGRAM_CHAT_ID, text)


def send_recovery_alert(service: str, classification: str, action: str) -> bool:
    """Send a recovery completed alert."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    text = (
        f"✅ *RECOVERY EXECUTED — {service.upper()}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Classification: `{classification}`\n"
        f"Action: `{action}`\n"
        f"Time: `{datetime.now().strftime('%H:%M:%S')}`"
    )
    return send_message(TELEGRAM_CHAT_ID, text)


def send_prediction_alert(service: str, metric: str, eta_s: int, current: float) -> bool:
    """Send a predictive breach alert."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    text = (
        f"🔮 *PREDICTED BREACH — {service.upper()}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Metric: `{metric.upper()}`\n"
        f"Current: `{current:.1f}`\n"
        f"Breach in: ~`{eta_s}s`"
    )
    return send_message(TELEGRAM_CHAT_ID, text)


# ═══════════════════════════════════════════════════════════════════════════════
# Command dispatcher
# ═══════════════════════════════════════════════════════════════════════════════

COMMANDS = {
    "/help":      cmd_help,
    "/status":    cmd_status,
    "/history":   cmd_history,
    "/predict":   cmd_predict,
    "/recover":   cmd_recover,
    "/threshold": cmd_threshold,
}


def handle_message(chat_id: str, text: str) -> None:
    text = text.strip()
    parts = text.split()
    if not parts:
        return

    cmd_raw = parts[0].split("@")[0].lower()
    args    = parts[1:]

    handler = COMMANDS.get(cmd_raw)
    if handler:
        logger.info("[TG BOT] Command %s from chat_id=%s args=%s", cmd_raw, chat_id, args)
        try:
            handler(chat_id, args)
        except Exception as exc:
            logger.error("[TG BOT] Handler error for %s: %s", cmd_raw, exc)
            send_message(chat_id, f"❌ Error: `{exc}`")
    else:
        send_message(chat_id,
            f"❓ Unknown command: `{cmd_raw}`\n"
            f"Type `/help` to see all commands.")


# ═══════════════════════════════════════════════════════════════════════════════
# Polling loop
# ═══════════════════════════════════════════════════════════════════════════════

def run_bot_loop() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("[TG BOT] No token configured — bot disabled")
        return

    logger.info("[TG BOT] Telegram bot started — polling every %ds", POLL_INTERVAL_S)
    send_message(
        TELEGRAM_CHAT_ID,
        "🤖 *AutoHealX AI Online*\nType `/help` for commands.",
    )

    offset = 0
    while True:
        updates = get_updates(offset)
        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text    = msg.get("text", "")

            if chat_id and chat_id == str(TELEGRAM_CHAT_ID) and text:
                handle_message(chat_id, text)
            elif chat_id and text:
                logger.warning(
                    "[TG BOT] Unauthorized message from chat_id=%s — ignored", chat_id
                )

        time.sleep(POLL_INTERVAL_S)


def start_bot_thread() -> threading.Thread:
    thread = threading.Thread(
        target=run_bot_loop,
        name="TelegramBot",
        daemon=True,
    )
    thread.start()
    logger.info("[TG BOT] Thread started — tid=%d", thread.ident)
    return thread


if __name__ == "__main__":
    run_bot_loop()