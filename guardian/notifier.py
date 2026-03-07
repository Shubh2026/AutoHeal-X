"""
notifier.py
════════════════════════════════════════════════════════════════════════════════
AutoHealX AI — Notification Engine

Sends alerts to:
  1. Slack   — via Incoming Webhook URL
  2. Telegram — via Bot API (token + chat_id)

Setup:
  Slack:
    • Go to https://api.slack.com/apps → Create App → Incoming Webhooks
    • Copy the Webhook URL into SLACK_WEBHOOK_URL below (or set env var)

  Telegram:
    • Message @BotFather on Telegram → /newbot → copy the token
    • Start a chat with your bot, then visit:
      https://api.telegram.org/bot<TOKEN>/getUpdates
    • Copy the chat_id from the response
    • Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID below (or env vars)

Environment variables (override hardcoded values):
  SLACK_WEBHOOK_URL
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
════════════════════════════════════════════════════════════════════════════════
"""

import logging
import os
import threading
import time
from datetime import datetime

import requests

logger = logging.getLogger("guardian.notifier")

# ── Config — set these or use environment variables ───────────────────────────
# Values are read from environment so secrets are not hardcoded in source.
SLACK_WEBHOOK_URL   = os.getenv("SLACK_WEBHOOK_URL",   "")
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN",  "")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID",    "")

# ── Cooldown — prevent notification spam ──────────────────────────────────────
NOTIFY_COOLDOWN_S   = 60    # min seconds between notifications for same service
_last_notify: dict  = {}    # service → last_notify_timestamp
_notify_lock        = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════════
# Cooldown guard
# ══════════════════════════════════════════════════════════════════════════════

def _can_notify(service: str, cooldown: int = NOTIFY_COOLDOWN_S) -> bool:
    """Returns True if enough time has passed since the last notification for this service."""
    with _notify_lock:
        last = _last_notify.get(service, 0)
        if time.time() - last >= cooldown:
            _last_notify[service] = time.time()
            return True
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Slack
# ══════════════════════════════════════════════════════════════════════════════

def send_slack(message: str, service: str = "global", urgency: str = "warning") -> bool:
    """
    Send a message to Slack via Incoming Webhook.
    Returns True on success, False on failure or if not configured.

    urgency: 'info' | 'warning' | 'critical'
    """
    if not SLACK_WEBHOOK_URL:
        logger.debug("[NOTIFY] Slack not configured — skipping")
        return False

    color_map = {
        "info":     "#36a64f",   # green
        "warning":  "#ffb700",   # amber
        "critical": "#ff3030",   # red
    }
    icon_map = {
        "info":     "ℹ️",
        "warning":  "⚠️",
        "critical": "🚨",
    }

    color = color_map.get(urgency, "#ffb700")
    icon  = icon_map.get(urgency, "⚠️")
    ts    = datetime.now().strftime("%H:%M:%S")

    payload = {
        "attachments": [
            {
                "color":    color,
                "fallback": f"{icon} AutoHealX AI | {service} | {message}",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"{icon} *AutoHealX AI* | `{service}` | _{ts}_"
                        }
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": message
                        }
                    }
                ]
            }
        ]
    }

    try:
        resp = requests.post(
            SLACK_WEBHOOK_URL,
            json=payload,
            timeout=5,
        )
        if resp.status_code == 200:
            logger.info("[NOTIFY] Slack ✓ — service=%s urgency=%s", service, urgency)
            return True
        else:
            logger.warning("[NOTIFY] Slack HTTP %d — %s", resp.status_code, resp.text[:80])
            return False
    except Exception as exc:
        logger.warning("[NOTIFY] Slack error: %s", exc)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Telegram
# ══════════════════════════════════════════════════════════════════════════════

def send_telegram(message: str, service: str = "global", urgency: str = "warning") -> bool:
    """
    Send a message to Telegram via Bot API.
    Returns True on success, False on failure or if not configured.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.debug("[NOTIFY] Telegram not configured — skipping")
        return False

    icon_map = {
        "info":     "ℹ️",
        "warning":  "⚠️",
        "critical": "🚨",
    }
    icon = icon_map.get(urgency, "⚠️")
    ts   = datetime.now().strftime("%H:%M:%S")

    text = (
        f"{icon} *AutoHealX AI Alert*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🖥 Service: `{service}`\n"
        f"🕐 Time: `{ts}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{message}"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       text,
                "parse_mode": "Markdown",
            },
            timeout=5,
        )
        data = resp.json()
        if data.get("ok"):
            logger.info("[NOTIFY] Telegram ✓ — service=%s urgency=%s", service, urgency)
            return True
        else:
            logger.warning("[NOTIFY] Telegram error: %s", data.get("description", "unknown"))
            return False
    except Exception as exc:
        logger.warning("[NOTIFY] Telegram error: %s", exc)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Unified notify — call this from guardian.py
# ══════════════════════════════════════════════════════════════════════════════

def notify_anomaly(
    service:        str,
    classification: str,
    score:          float,
    metrics:        dict,
    action_taken:   str | None = None,
) -> None:
    """
    Send anomaly notification to all configured channels.
    Respects cooldown — won't spam if the same service keeps firing.

    Called by guardian.py when score > 0.65.
    """
    if not _can_notify(service):
        logger.debug("[NOTIFY] Cooldown active for service=%s — skipping", service)
        return

    urgency = "critical" if score > 0.65 else "warning"

    cpu  = metrics.get("cpu",  0)
    mem  = metrics.get("mem",  0)
    disk = metrics.get("disk", 0)
    net  = metrics.get("net",  0)

    action_line = f"\n🔧 *Recovery fired:* `{action_taken}`" if action_taken else ""

    message = (
        f"*{classification}* detected\n"
        f"📊 Score: `{score:.3f}` {'🔴 CRITICAL' if score > 0.65 else '🟡 WARNING'}\n"
        f"CPU: `{cpu:.1f}%` | MEM: `{mem:.1f}%` | "
        f"DISK: `{disk:.1f}%` | NET: `{net:.0f} KB/s`"
        f"{action_line}"
    )

    # Fire both channels in parallel threads (non-blocking)
    threading.Thread(
        target=send_slack,
        args=(message, service, urgency),
        daemon=True,
    ).start()

    threading.Thread(
        target=send_telegram,
        args=(message, service, urgency),
        daemon=True,
    ).start()


def notify_recovery(service: str, action: str, result: str) -> None:
    """
    Send recovery confirmation notification.
    Called by guardian.py after a recovery action completes.
    """
    message = (
        f"✅ *Recovery complete*\n"
        f"Action: `{action}`\n"
        f"Result: {result[:100]}"
    )
    threading.Thread(
        target=send_slack,
        args=(message, service, "info"),
        daemon=True,
    ).start()
    threading.Thread(
        target=send_telegram,
        args=(message, service, "info"),
        daemon=True,
    ).start()


def notify_prediction(service: str, metric: str, eta_seconds: int, current_val: float) -> None:
    """
    Send a PREDICTIVE alert — metric is trending toward threshold.
    Called by anomaly_detector.predict_breach() when trend detected.
    """
    if not _can_notify(f"predict_{service}_{metric}", cooldown=120):
        return

    message = (
        f"🔮 *Predicted breach in ~{eta_seconds}s*\n"
        f"Metric: `{metric.upper()}` currently at `{current_val:.1f}`\n"
        f"Trending toward critical threshold — intervention recommended"
    )
    threading.Thread(
        target=send_slack,
        args=(message, service, "warning"),
        daemon=True,
    ).start()
    threading.Thread(
        target=send_telegram,
        args=(message, service, "warning"),
        daemon=True,
    ).start()