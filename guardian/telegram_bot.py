"""
telegram_bot.py
════════════════════════════════════════════════════════════════════════════════
AutoHealX AI — Telegram Command Bot

A developer can interact with the live system from their phone:

  /status              → live metrics + anomaly score for all services
  /status web          → metrics for a specific service
  /history             → last 10 events from the decision log
  /predict             → current trend analysis — which metrics are rising?
  /recover <service>   → manually trigger recovery for a service
  /threshold <value>   → change the anomaly threshold (e.g. /threshold 0.70)
  /help                → list all commands

Setup:
  1. Message @BotFather on Telegram → /newbot
  2. Copy the bot token into TELEGRAM_BOT_TOKEN in notifier.py (or env var)
  3. Start a chat with your bot
  4. Visit https://api.telegram.org/bot<TOKEN>/getUpdates
  5. Copy your chat_id from the "from" object
  6. Set TELEGRAM_CHAT_ID in notifier.py (or env var)
  7. python telegram_bot.py runs standalone, OR guardian.py starts it as a thread

Architecture:
  • Polls Telegram's getUpdates endpoint every 2 seconds (long-polling)
  • Reads live state from guardian.py's shared _live_state dict (imported)
  • Sends replies via sendMessage API
  • Runs as a daemon thread — dies when guardian.py exits
════════════════════════════════════════════════════════════════════════════════
"""

import logging
import os
import threading
import time
from datetime import datetime

import requests

logger = logging.getLogger("guardian.telegram_bot")

# ── Config ──────────────────────────────
# Tokens and chat IDs are read from environment for security:
#   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
POLL_INTERVAL_S    = 2      # seconds between getUpdates calls
REQUEST_TIMEOUT_S  = 10



# ══════════════════════════════════════════════════════════════════════════════
# Telegram API helpers
# ══════════════════════════════════════════════════════════════════════════════

def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"


def send_message(chat_id: str, text: str, parse_mode: str = "Markdown") -> bool:
    """Send a message to a chat. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN:
        return False
    try:
        resp = requests.post(
            _api_url("sendMessage"),
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=REQUEST_TIMEOUT_S,
        )
        return resp.json().get("ok", False)
    except Exception as exc:
        logger.warning("[TG BOT] send_message error: %s", exc)
        return False


def get_updates(offset: int) -> list:
    """Long-poll Telegram for new messages. Returns list of update objects."""
    if not TELEGRAM_BOT_TOKEN:
        return []
    try:
        resp = requests.get(
            _api_url("getUpdates"),
            params={"offset": offset, "timeout": 5},
            timeout=REQUEST_TIMEOUT_S,
        )
        data = resp.json()
        return data.get("result", []) if data.get("ok") else []
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════════════════
# State accessor — reads from guardian.py's shared live state
# ══════════════════════════════════════════════════════════════════════════════

def _get_state() -> dict:
    """
    Import guardian's live state at runtime (avoids circular import at module load).
    Falls back to empty dict if guardian isn't running.
    """
    try:
        import guardian
        return guardian.get_state_snapshot()
    except Exception:
        return {}


def _get_multi_state() -> dict:
    """
    Get state for all services from the multi-service guardian.
    Returns dict keyed by service name.
    """
    try:
        import guardian
        return guardian.get_all_services_snapshot()
    except Exception:
        return {}


def _get_history() -> list:
    """Get event history from guardian's live state."""
    state = _get_state()
    return state.get("events", [])


# ══════════════════════════════════════════════════════════════════════════════
# Command handlers
# ══════════════════════════════════════════════════════════════════════════════

def cmd_help(chat_id: str, _args: list) -> None:
    text = (
        "🤖 *AutoHealX AI — Command Reference*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
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

    # Single service requested
    if args:
        service_name = args[0].lower()
        services = state.get("services", {})
        if service_name not in services:
            available = ", ".join(services.keys()) or "none"
            send_message(chat_id,
                f"❌ Service `{service_name}` not found.\nAvailable: {available}")
            return
        svc = services[service_name]
        _send_service_status(chat_id, service_name, svc)
        return

    # All services
    services = state.get("services", {})
    if not services:
        # Single-service fallback
        _send_single_status(chat_id, state)
        return

    lines = [
        f"📊 *AutoHealX AI — All Services*",
        f"🕐 `{datetime.now().strftime('%H:%M:%S')}`  |  "
        f"Tick `{state.get('tick', '?')}`",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for name, svc in services.items():
        score  = svc.get("score", 0)
        sev    = svc.get("severity", "?")
        online = svc.get("online", False)
        m      = svc.get("metrics", {})

        sev_icon = "🔴" if sev == "CRITICAL" else "🟡" if sev == "WARNING" else "🟢"
        status   = "🟢 ONLINE" if online else "🔴 OFFLINE"

        lines.append(
            f"\n*{name.upper()}* {sev_icon} {status}\n"
            f"Score: `{score:.3f}` | CPU: `{m.get('cpu',0):.1f}%` | "
            f"MEM: `{m.get('mem',0):.1f}%`\n"
            f"DISK: `{m.get('disk',0):.1f}%` | NET: `{m.get('net',0):.0f} KB/s`"
        )

    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(
        f"Recoveries: `{state.get('recoveries_total',0)}` | "
        f"Anomalies: `{state.get('anomalies_total',0)}`"
    )
    send_message(chat_id, "\n".join(lines))


def _send_service_status(chat_id: str, name: str, svc: dict) -> None:
    score   = svc.get("score", 0)
    sev     = svc.get("severity", "UNKNOWN")
    clf     = svc.get("classification", "—")
    m       = svc.get("metrics", {})
    action  = svc.get("action") or "None"
    online  = svc.get("online", False)

    sev_icon = "🔴" if sev == "CRITICAL" else "🟡" if sev == "WARNING" else "🟢"
    status   = "🟢 ONLINE" if online else "🔴 OFFLINE"

    text = (
        f"📊 *{name.upper()}* — Live Status\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Status: {status}\n"
        f"Severity: {sev_icon} `{sev}`\n"
        f"Score: `{score:.3f}` / 1.000\n"
        f"Classification: `{clf}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"CPU:  `{m.get('cpu',0):.1f}%`\n"
        f"MEM:  `{m.get('mem',0):.1f}%`\n"
        f"DISK: `{m.get('disk',0):.1f}%`\n"
        f"NET:  `{m.get('net',0):.0f} KB/s`\n"
        f"PROCS: `{m.get('procs',0)}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Last action: `{action}`"
    )
    send_message(chat_id, text)


def _send_single_status(chat_id: str, state: dict) -> None:
    """Fallback for single-service guardian."""
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
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Severity: {sev_icon} `{sev}`\n"
        f"Score: `{score:.3f}` / 1.000\n"
        f"Class: `{clf}`\n"
        f"Baseline: {bl_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"CPU:  `{m.get('cpu',0):.1f}%`\n"
        f"MEM:  `{m.get('mem',0):.1f}%`\n"
        f"DISK: `{m.get('disk',0):.1f}%`\n"
        f"NET:  `{m.get('net',0):.0f} KB/s`\n"
        f"PROCS: `{m.get('procs',0)}`"
    )
    send_message(chat_id, text)


def cmd_history(chat_id: str, args: list) -> None:
    """Show last N events from the decision log."""
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
    """Show trend predictions for all metrics."""
    try:
        import anomaly_detector as ad

        state     = _get_state()
        services  = state.get("services", {state.get("classification","?"): state})
        lines     = ["🔮 *AutoHealX AI — Trend Predictions*\n"]

        for svc_name in (services if isinstance(services, dict) and services else ["main"]):
            cpu_hist  = ad.learner.get_history("cpu")
            mem_hist  = ad.learner.get_history("mem")
            disk_hist = ad.learner.get_history("disk")
            net_hist  = ad.learner.get_history("net")

            predictions = ad.predict_breach(cpu_hist, mem_hist, disk_hist, net_hist)

            if not predictions:
                lines.append(f"✅ *{svc_name}*: All metrics stable — no breach predicted")
            else:
                lines.append(f"⚠️ *{svc_name}* — Predicted breaches:")
                for pred in predictions:
                    lines.append(
                        f"  `{pred['metric'].upper()}` — current `{pred['current']:.1f}` → "
                        f"threshold `{pred['threshold']:.1f}` in ~`{pred['eta_s']}s`"
                    )
            lines.append("")

        send_message(chat_id, "\n".join(lines))

    except Exception as exc:
        send_message(chat_id, f"⚠️ Prediction error: `{exc}`")


def cmd_recover(chat_id: str, args: list) -> None:
    """Manually trigger recovery for a named service."""
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
    """Change the anomaly score threshold live."""
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


# ══════════════════════════════════════════════════════════════════════════════
# Command dispatcher
# ══════════════════════════════════════════════════════════════════════════════

COMMANDS = {
    "/help":      cmd_help,
    "/status":    cmd_status,
    "/history":   cmd_history,
    "/predict":   cmd_predict,
    "/recover":   cmd_recover,
    "/threshold": cmd_threshold,
}


def handle_message(chat_id: str, text: str) -> None:
    """Parse and dispatch a Telegram message to the correct handler."""
    text = text.strip()
    parts = text.split()
    if not parts:
        return

    # Extract command (handle /command@botname format)
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


# ══════════════════════════════════════════════════════════════════════════════
# Polling loop
# ══════════════════════════════════════════════════════════════════════════════

def run_bot_loop() -> None:
    """
    Main Telegram polling loop.
    Runs forever in a daemon thread.
    Only responds to messages from the configured TELEGRAM_CHAT_ID
    (prevents unauthorized control of the system).
    """
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

            # Security: only accept messages from the configured chat
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