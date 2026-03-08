# AI Self-Healing Virtual Server System

An autonomous infrastructure monitoring system that detects anomalies using machine learning (IsolationForest) and executes targeted recovery actions — no human intervention required.



<div align="center">

# 🛡️ AutoHealX AI

### Autonomous Self-Healing Infrastructure System

[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white)](https://python.org)
[![Docker](https://img.shields.io/badge/Docker-Containerized-2496ED?logo=docker&logoColor=white)](https://docker.com)
[![ML](https://img.shields.io/badge/ML-IsolationForest-green?logo=scikit-learn&logoColor=white)](https://scikit-learn.org)
[![Telegram](https://img.shields.io/badge/Telegram-Bot%20Enabled-26A5E4?logo=telegram&logoColor=white)](https://core.telegram.org/bots)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

*An AI-powered system that monitors, detects anomalies, classifies failures, and automatically executes targeted recovery actions — all without human intervention.*

---

[Features](#-features) • [Architecture](#-architecture) • [Quick Start](#-quick-start) • [Demo](#-demo) • [Telegram Bot](#-telegram-bot) • [Guardian UI](#-guardian-ui)

</div>

---

## 🧠 What is AutoHealX AI?

AutoHealX AI is a **self-healing virtual infrastructure** that demonstrates autonomous server recovery using machine learning. It monitors a cluster of Dockerized microservices, detects anomalies using an **IsolationForest-based ML model**, classifies the failure type, and automatically executes the correct recovery action — all in real time.

### The Self-Healing Loop

```
┌─────────────┐     ┌──────────────┐     ┌────────────────┐     ┌──────────────┐
│  📊 Collect  │ ──▶ │  🧠 Detect   │ ──▶ │  🏷️ Classify   │ ──▶ │  🔧 Recover  │
│   Metrics    │     │   Anomaly    │     │   Fault Type   │     │   Auto-Fix   │
└─────────────┘     └──────────────┘     └────────────────┘     └──────────────┘
       ▲                                                               │
       └───────────────────── continuous loop ◀────────────────────────┘

```
**Key insight:** The system goes from *"anomaly detected"* to *"recovery executed"* in under 5 seconds, with zero human intervention.


## ✨ Features

### 🤖 AI-Powered Detection
- **IsolationForest ML model** trained on 50-sample baseline of normal behavior
- **Blended scoring:** 60% IsolationForest + 40% statistical z-score
- **Anomaly score 0.0–1.0** with configurable threshold (default: 0.65)
- **Predictive breach detection** using linear regression trend analysis

### 🏷️ Intelligent Classification
| Fault Class | Trigger Condition | Recovery Action |
|------------|-------------------|-----------------|
| `CPU_OVERLOAD` | CPU > 80% or rising fast | Kill heavy process → restart container |
| `MEMORY_LEAK` | Memory > 80% or rising | Restart container |
| `DISK_PRESSURE` | Disk > 90% or rising | Cleanup temp files |
| `TRAFFIC_SPIKE` | Network > 100 KB/s | Activate rate limiting |
| `PROCESS_CRASH` | CPU < 2% AND Memory < 2% | Auto-restart on crash |
| `ANOMALY_DETECTED` | Any metric above warning | Activate safe mode |

### 🔧 Autonomous Recovery
- **6 recovery actions** executed via Docker SDK
- **Fallback logic:** if primary action fails, escalates to container restart
- **Thread-safe:** concurrent recovery operations protected by locks
- **Full audit trail** in `guardian.log` with timestamps and results

### 📡 Multi-Service Monitoring
- **4 independent microservices** (web, API, database, cache)
- Each service builds its **own ML baseline independently**
- Per-service anomaly scoring, classification, and recovery
- Cluster-wide health aggregation


### 🤖 Telegram Bot
- `/status` — live metrics for all services
- `/history` — recent anomaly events
- `/predict` — trend-based breach predictions
- `/recover <service>` — manual recovery trigger
- `/threshold <value>` — change detection sensitivity
- **Proactive alerts** sent automatically on anomaly detection

### 🧪 Chaos Engineering
- **5 chaos injection types** via REST API
- CPU spike (multi-threaded GIL-releasing hash computation)
- Memory leak (progressive 1MB allocations)
- Disk flood (100MB write to tmpfs)
- Traffic spike (50 concurrent request threads)
- Multi-vector (combined attack)

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        GUARDIAN (AI Brain)                        │
│                                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │  Anomaly     │  │  Recovery    │  │  Notification Engine   │  │
│  │  Detector    │  │  Engine      │  │  (Slack + Telegram)    │  │
│  │              │  │              │  │                        │  │
│  │ IsolationFor │  │ Docker SDK   │  │  Proactive alerts      │  │
│  │ + Z-Score    │  │ 6 actions    │  │  Cooldown guard        │  │
│  │ Trend detect │  │ Fallback     │  │  Predictive warnings   │  │
│  └──────┬───────┘  └──────┬───────┘  └────────────┬───────────┘  │
│         │                 │                       │              │
│  ┌──────┴─────────────────┴───────────────────────┴───────────┐  │
│  │              Guardian Main Loop (5s tick)                   │  │
│  └────────────────────────────┬────────────────────────────────┘  │
│                               │                                  │
│  ┌────────────┐  ┌────────────┴──────┐  ┌─────────────────────┐  │
│  │ State API  │  │ REST API          │  │ Telegram Bot        │  │
│  │ :5001      │  │ :5002             │  │ Polling Thread      │  │
│  └─────┬──────┘  └───────────────────┘  └─────────────────────┘  │
└────────┼─────────────────────────────────────────────────────────┘
         │
    ┌────┴────┐
    │ Guardian│          ┌─────────────────────────────────────────┐
    │   UI    │          │         DOCKER CLUSTER                  │
    │ (HTML)  │          │                                         │
    └─────────┘          │  ┌────────┐ ┌────────┐ ┌────────────┐  │
                         │  │  web   │ │  api   │ │  database  │  │
                         │  │ :5000  │ │ :5010  │ │   :5020    │  │
                         │  └────────┘ └────────┘ └────────────┘  │
                         │  ┌────────┐                            │
                         │  │ cache  │   Flask + psutil + chaos   │
                         │  │ :5030  │   injection endpoints      │
                         │  └────────┘                            │
                         └─────────────────────────────────────────┘
```

---

## 🚀 Quick Start

---

### Prerequisites

| Requirement | Version | Check |
|---|---|---|
| Docker Desktop | 24+ | `docker --version` |
| Docker Compose | V2 | `docker compose version` |
| Python | 3.11+ | `python3 --version` |
| pip | any | `pip --version` |
| A modern browser | Chrome / Firefox | — |

> **Windows users:** Run all terminal commands in PowerShell or Windows Terminal. Docker Desktop must be running before you start.

---


### 1. Clone the Repository

```bash
git clone https://github.com/Shubh2026/AutoHeal-X.git
cd AutoHeal-X
```

### 2. Create Virtual Environment

```bash
python -m venv venv

# Windows PowerShell
.\venv\Scripts\Activate.ps1

# Linux/Mac
source venv/bin/activate
```

### 3. Install Guardian Dependencies

```bash
pip install -r guardian/requirements_guardian.txt
```

### 4. Start the Docker Cluster

```bash
cd server
docker compose up --build -d
cd ..
```

### 5. Start the Guardian

```bash
cd guardian
python guardian.py
```

### 6. 📊 Running the Dashboard

Navigate to the dashboard folder and start a local server:
```
python -m http.server 8000
```
Then open your browser:
```
http://localhost:8000/dashboard_real.html
```


## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        HOST MACHINE                             │
│                                                                 │
│  ┌──────────────────────┐     ┌──────────────────────────────┐  │
│  │   TERMINAL 2         │     │   TERMINAL 3                 │  │
│  │   dashboard/         │     │   guardian/                  │  │
│  │                      │     │                              │  │
│  │  dashboard_real.html │     │  guardian.py                 │  │
│  │  (browser)           │     │  ├── anomaly_detector.py     │  │
│  │                      │     │  │   └── IsolationForest     │  │
│  │  • Live metric cards │     │  ├── recovery_engine.py      │  │
│  │  • Anomaly score ring│     │  │   └── docker SDK          │  │
│  │  • Event log         │     │  └── heartbeat_monitor.py    │  │
│  │  • Chaos Mode button │     │      └── /health thread      │  │
│  └──────────┬───────────┘     └──────────────┬───────────────┘  │
│             │  GET /metrics (every 1s)        │ GET /metrics (5s)│
│             │  POST /chaos                    │ GET /health  (3s)│
│             │                                 │ docker restart   │
│  ┌──────────▼─────────────────────────────────▼───────────────┐  │
│  │                   TERMINAL 1                                │  │
│  │                   server/                                  │  │
│  │                                                             │  │
│  │   ┌─────────────────────────────────────────────────────┐  │  │
│  │   │           Docker Container: ai-server               │  │  │
│  │   │           Flask  →  localhost:5000                  │  │  │
│  │   │                                                     │  │  │
│  │   │   GET  /metrics  →  cpu, mem, disk, net, procs      │  │  │
│  │   │   GET  /health   →  { status: ok, uptime_seconds }  │  │  │
│  │   │   POST /chaos    →  inject real stress (15s)        │  │  │
│  │   └─────────────────────────────────────────────────────┘  │  │
│  └─────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘

Data flow:
  Server ──metrics──▶ Dashboard (visualise)
  Server ──metrics──▶ Guardian (detect + recover)
  Guardian ──docker SDK──▶ Container (restart / exec)
  Dashboard ──POST /chaos──▶ Server (inject failure)
```

### 7. Inject Chaos (Test Recovery)

```bash
# CPU spike
curl -X POST "http://localhost:5000/chaos?type=cpu_spike"

# Memory leak
curl -X POST "http://localhost:5000/chaos?type=memory_leak"

# Disk flood
curl -X POST "http://localhost:5000/chaos?type=disk_flood"

# Traffic spike
curl -X POST "http://localhost:5000/chaos?type=traffic_spike"

# Multi-vector attack
curl -X POST "http://localhost:5000/chaos?type=multi_vector"
```

Watch the Guardian UI — you'll see the anomaly score spike, classification change, and recovery execute automatically! ⚡

---

## 🎬 Demo

### Demo Flow (for judges / presentation)

1. **Start the system** (Steps 4-6 above)
2. **Wait ~4 minutes** for baseline training (50 samples)
3. **Show normal state** — green scores, NORMAL classification
4. **Inject CPU spike** — watch score jump to 0.9+, classification → CPU_OVERLOAD
5. **Recovery fires automatically** — container restart, score drops back
6. **Show Telegram alert** — notification received on phone
7. **Show Guardian UI** — critical recovery banner, decision log
8. **Inject disk flood** — disk pressure detected, cleanup executed
9. **Show predictive alerts** — system warns BEFORE breach occurs

### Expected Timeline

| Time | Event |
|------|-------|
| 0:00 | Start Guardian |
| 0:00–4:10 | Baseline learning (50 samples at 5s intervals) |
| 4:10 | ✅ IsolationForest trained — Guardian ACTIVE |
| 4:15 | Inject chaos (e.g., cpu_spike) |
| 4:20 | 🔴 Anomaly detected (score > 0.65) |
| 4:21 | 🔧 Recovery executed automatically |
| 4:25 | ✅ System normalized |

---

## 🤖 Telegram Bot

### Setup

1. Message **@BotFather** on Telegram → `/newbot`
2. Copy the bot token
3. Start a chat with your bot, then visit:
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
4. Copy your `chat_id` from the response
5. Set environment variables before starting Guardian:

```bash
# Windows PowerShell
$env:TELEGRAM_BOT_TOKEN = "your_bot_token"
$env:TELEGRAM_CHAT_ID = "your_chat_id"

# Linux/Mac
export TELEGRAM_BOT_TOKEN="your_bot_token"
export TELEGRAM_CHAT_ID="your_chat_id"
```

### Commands

| Command | Description |
|---------|-------------|
| `/status` | Live metrics for all 4 services |
| `/status web` | Metrics for a specific service |
| `/history` | Last 10 anomaly events |
| `/history 20` | Last 20 events |
| `/predict` | Trend predictions — which metrics are rising? |
| `/recover web` | Manually trigger recovery |
| `/threshold 0.70` | Change detection sensitivity |
| `/help` | List all commands |

### Proactive Alerts

The bot automatically sends alerts when:
- 🚨 **Anomaly detected** (score > 0.65)
- ✅ **Recovery completed** (action result)
- 🔮 **Breach predicted** (metric trending toward threshold)

---


### Access

Open `guardian/guardian_ui.html` in any modern browser while `guardian.py` is running.

---

## 📁 Project Structure

```
AutoHealX-AI/
├── server/
│   ├── server.py              # Flask app: /metrics, /health, /chaos endpoints
│   ├── requirements.txt       # Flask, psutil, flask-cors
│   ├── Dockerfile             # Python 3.11-slim + procps + curl
│   └── docker-compose.yml     # 4-service cluster (web, api, database, cache)
│
├── guardian/
│   ├── guardian.py            # Main orchestrator — polling loop + state API
│   ├── anomaly_detector.py    # IsolationForest + z-score + classification
│   ├── recovery_engine.py     # Docker SDK recovery actions (6 actions)
│   ├── heartbeat_monitor.py   # Health endpoint monitor (daemon thread)
│   ├── guardian_api.py        # REST API on port 5002
│   ├── notifier.py            # Slack + Telegram notification engine
│   ├── telegram_bot.py        # Interactive Telegram bot with commands
│   ├── guardian_ui.html       # Military-style HUD dashboard
│   └── requirements_guardian.txt
│
├── dashboard/
│   └── dashboard_real.html    # Metrics visualization dashboard
│
├── test_integration.py        # End-to-end test suite (6 tests)
├── demo_script.md             # Step-by-step demo instructions
└── README.md                  # This file
```

---

## 🧪 Testing

### Run Integration Tests

```bash
python test_integration.py
```

### Test Suite Coverage

| Test | What It Verifies |
|------|-----------------|
| Test 1 | `/metrics` returns all 6 required fields |
| Test 2 | `/health` returns `{status: ok}` |
| Test 3 | `POST /chaos?type=cpu_spike` returns HTTP 200 |
| Test 4 | CPU spikes above 60% within 15 seconds after chaos |
| Test 5 | Anomaly detector scores chaos > 0.4 (in-process) |
| Test 6 | Full recovery loop — chaos → detection → recovery action logged |

---

## ⚙️ Configuration

### Anomaly Detection Thresholds

| Parameter | Value | Description |
|-----------|-------|-------------|
| `ANOMALY_SCORE_THRESHOLD` | 0.65 | Score above this triggers recovery |
| `CPU_WARN / CPU_CRIT` | 60% / 80% | CPU warning and critical thresholds |
| `MEM_WARN / MEM_CRIT` | 60% / 80% | Memory thresholds |
| `DISK_WARN / DISK_CRIT` | 70% / 90% | Disk thresholds |
| `NET_WARN / NET_CRIT` | 50 KB/s / 100 KB/s | Network rate thresholds |
| `BASELINE_SIZE` | 50 | Samples before ML model trains |

### ML Model Parameters

| Parameter | Value |
|-----------|-------|
| Algorithm | IsolationForest |
| Trees | 200 (`n_estimators`) |
| Contamination | 0.05 (5%) |
| Score blend | 60% IF + 40% z-score |
| Sigmoid shift | 2.5 (calibrated for low false positives) |

### Ports

| Port | Service |
|------|---------|
| 5000 | Web service (primary Flask app) |
| 5010 | API service |
| 5020 | Database service |
| 5030 | Cache service |
| 5001 | Guardian State API (UI polls this) |
| 5002 | Guardian REST API |

---

## 🛠️ API Reference

### Server Endpoints (port 5000)

```http
GET /metrics
```
Returns real-time system metrics:
```json
{
  "cpu_percent": 3.2,
  "memory_percent": 45.1,
  "disk_percent": 12.5,
  "net_bytes_sent": 1024,
  "net_bytes_recv": 2048,
  "active_processes": 142
}
```

```http
GET /health
```
Returns health status:
```json
{
  "status": "ok",
  "uptime_seconds": 3600
}
```

```http
POST /chaos?type=<chaos_type>
```
Injects chaos. Types: `cpu_spike`, `memory_leak`, `disk_flood`, `traffic_spike`, `multi_vector`

---

## 🔮 How the ML Model Works

### Training Phase (Baseline)
1. Collects 50 samples of normal system behavior (CPU, Memory, Disk, Network)
2. Trains IsolationForest with 200 trees and 5% contamination
3. Calibrates scoring sigmoid using training distribution (mean + std)
4. Stores per-metric baseline stats for z-score calculations

### Scoring Phase (Live)
1. **IsolationForest** scores the new sample against the trained model
2. Raw score is normalized via sigmoid: `score = 1 / (1 + exp(-(z - 2.5)))`
3. **Z-score** calculates per-metric deviation from baseline
4. Final score = **60% IF + 40% z-score**, clipped to [0, 1]

### Classification
Priority-ordered rule matching based on metric values and trends:
```
PROCESS_CRASH > TRAFFIC_SPIKE > CPU_OVERLOAD > MEMORY_LEAK > DISK_PRESSURE > ANOMALY_DETECTED > NORMAL
```

### Predictive Detection
Linear regression over the last 10 samples extrapolates when metrics will breach thresholds. Warns if breach predicted within 120 seconds.

---

## 👥 Team

## Anomaly - Shubh , Shubham , Keshav , Rashmita.

---

## Running Integration Tests

With the server running in Terminal 1:

```bash
cd ..
python test_integration.py
```

All 6 tests should print **PASS** in green. See test output for details if any fail.

---

## Stopping Everything

```bash
# Stop the Guardian (Terminal 3)
Ctrl-C

# Stop the server (Terminal 1)
Ctrl-C
# Then remove the container:
docker compose down

# Verify container is gone:
docker ps
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Dashboard shows SERVER OFFLINE | Check Terminal 1 — container must be running |
| `docker compose up` fails with port conflict | Another process is on port 5000: `lsof -i :5000` then kill it |
| Guardian shows `Cannot connect to Docker daemon` | Docker Desktop is not running — start it first |
| Guardian shows `Container 'ai-server' not found` | Container name mismatch — check `docker ps` and update `CONTAINER_NAME` in `recovery_engine.py` |
| `ImportError: No module named sklearn` | Run `pip install -r guardian/requirements_guardian.txt` |
| Anomaly score stays at 0.00 | Baseline still learning — wait for 50 ticks (~4 minutes) |
| Recovery fires immediately | Chaos was triggered during baseline learning — restart guardian |

---

## How It Works (Technical Summary)

**Anomaly Detection:**
The Guardian collects 50 normal-operation metric samples to build a baseline, then trains an `IsolationForest` model (sklearn). Each new reading is scored: the model's `score_samples()` output is normalised via a sigmoid anchored to the training distribution (not hardcoded constants), giving a reliable 0.0–1.0 anomaly score. A 40% per-metric z-score component catches single-metric spikes the forest might miss.

**Classification:**
Rule-based priority classifier maps metric patterns to fault types: `PROCESS_CRASH` (cpu+mem ≈ 0), `TRAFFIC_SPIKE` (high net), `CPU_OVERLOAD`, `MEMORY_LEAK`, `DISK_PRESSURE`, `ANOMALY_DETECTED` (generic), `NORMAL`.


**Recovery:**
Six targeted Docker actions: container restart, kill heaviest process (exec), rate-limit flag, /tmp cleanup (exec), auto-restart on crash (heartbeat-triggered), safe mode (kill stress processes).

**Heartbeat:**
Independent daemon thread pings `/health` every 3 seconds. Three consecutive failures trigger `auto_restart_on_crash()` with a 30-second cooldown.



<div align="center">

**Built with 🧠 AI + 🐳 Docker + 🐍 Python**

*AutoHealX AI — Because servers should fix themselves.*

