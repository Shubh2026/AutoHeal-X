# AI Self-Healing Virtual Server System

An autonomous infrastructure monitoring system that detects anomalies using machine learning (IsolationForest) and executes targeted recovery actions — no human intervention required.

---

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

---

## Prerequisites

| Requirement | Version | Check |
|---|---|---|
| Docker Desktop | 24+ | `docker --version` |
| Docker Compose | V2 | `docker compose version` |
| Python | 3.11+ | `python3 --version` |
| pip | any | `pip --version` |
| A modern browser | Chrome / Firefox | — |

> **Windows users:** Run all terminal commands in PowerShell or Windows Terminal. Docker Desktop must be running before you start.

---

## Project Structure

```
ai-self-healing-server/
├── server/
│   ├── server.py               # Flask app — /metrics /health /chaos
│   ├── requirements.txt        # flask flask-cors psutil
│   ├── Dockerfile
│   └── docker-compose.yml
│
├── dashboard/
│   └── dashboard_real.html     # Open directly in browser — no server needed
│
├── guardian/
│   ├── guardian.py             # Main loop — entry point
│   ├── anomaly_detector.py     # IsolationForest + z-score
│   ├── recovery_engine.py      # 6 recovery actions via Docker SDK
│   ├── heartbeat_monitor.py    # Daemon thread — pings /health every 3s
│   └── requirements_guardian.txt
│
├── test_integration.py         # 6 automated integration tests
├── demo_script.md              # 60-second judge demo guide
└── README.md
```

---

## Setup

### Step 1 — Clone / download the project

```bash
git clone <your-repo-url> ai-self-healing-server
cd ai-self-healing-server
```

### Step 2 — Verify Docker is running

```bash
docker info
# Should print Docker system info — NOT "Cannot connect to Docker daemon"
```

### Step 3 — Install Guardian dependencies

```bash
cd guardian
pip install -r requirements_guardian.txt
cd ..
```

That's it. The server runs inside Docker so no separate Python install is needed for it.

---

## Running the System (3 Terminals)

Open **three separate terminal windows** and run one command in each.

---

### TERMINAL 1 — Start the Server

```bash
cd ai-self-healing-server/server
docker compose up --build
```

**Expected output:**
```
[+] Building ...
[+] Running 1/1
 ✔ Container ai-server  Started
ai-server  |  * Running on http://0.0.0.0:5000
ai-server  |  * Debug mode: off
```

**Verify it works:**
```bash
curl http://localhost:5000/health
# {"status": "ok", "uptime_seconds": 12}

curl http://localhost:5000/metrics
# {"cpu_percent": 4.2, "memory_percent": 38.1, "disk_percent": 61.0,
#  "net_bytes_sent": 1234, "net_bytes_recv": 5678, "active_processes": 87}
```

---

### TERMINAL 2 — Open the Dashboard

No command needed. Just open the file in your browser:

- **Mac/Linux:** `open dashboard/dashboard_real.html`
- **Windows:** Double-click `dashboard/dashboard_real.html` in Explorer
- **Or:** drag the file into Chrome/Firefox

The header should show **● SERVER ONLINE** within 2 seconds.

If you see **✕ SERVER OFFLINE**, check that Terminal 1 is running and the container started successfully.

---

### TERMINAL 3 — Start the AI Guardian

```bash
cd ai-self-healing-server/guardian
python guardian.py
```

**Expected output (first 4 minutes — baseline learning):**
```
════════════════════════════════════════════════════════════════════════════
  AI GUARDIAN MONITOR  —  Stage 3
  server=http://localhost:5000  poll=5s  threshold=0.65
  Press Ctrl-C to stop.
════════════════════════════════════════════════════════════════════════════

── TICK    1  10:23:01 ────────────────────────────────────────────────────
  [BASELINE] Learning baseline... (1/50)
  CPU    4.2%   MEM   38.1%   DISK  61.0%   NET    12.4 KB/s   PROCS   87
  SCORE 0.000   SEVERITY NORMAL     CLASS NORMAL
  HB UP
```

After 50 ticks (~4 minutes):
```
  [BASELINE] ✓ Baseline ready — IsolationForest trained on 50 samples.
  [IF] score_samples: mu=-0.502  sig=0.031  range=[-0.598, -0.451]
```

From this point, anomaly scoring and recovery actions are live.

---

## Demonstrating Chaos Mode

### Option A — Via the Dashboard (recommended for judges)

1. In the dashboard, select a chaos type from the dropdown:
   - `CPU Spike` — burns CPU inside the container
   - `Memory Leak` — allocates memory rapidly
   - `Disk Flood` — writes a large file to /tmp
   - `Traffic Spike` — sends 50 concurrent requests
   - `Multi-Vector` — CPU + Memory + Network simultaneously

2. Click **INJECT**

3. Watch in real time:
   - The relevant threshold meter turns **RED**
   - The anomaly score ring climbs toward 1.0
   - The event log records the anomaly type + score
   - In Terminal 3, Guardian prints a **RED** recovery action line

### Option B — Via curl

```bash
# CPU spike (most dramatic for live demo)
curl -X POST "http://localhost:5000/chaos?type=cpu_spike"

# Memory leak
curl -X POST "http://localhost:5000/chaos?type=memory_leak"

# Disk flood
curl -X POST "http://localhost:5000/chaos?type=disk_flood"

# Traffic spike
curl -X POST "http://localhost:5000/chaos?type=traffic_spike"

# All at once (most impressive)
curl -X POST "http://localhost:5000/chaos?type=multi_vector"
```

### Expected Recovery Behavior

| Chaos Type | Guardian Classification | Recovery Action |
|---|---|---|
| cpu_spike | CPU_OVERLOAD | Kill heaviest process inside container |
| memory_leak | MEMORY_LEAK | Restart container |
| disk_flood | DISK_PRESSURE | Delete /tmp/*.log files inside container |
| traffic_spike | TRAFFIC_SPIKE | Activate rate limiting flag |
| multi_vector | TRAFFIC_SPIKE or CPU_OVERLOAD | Kill process or rate limit |

Recovery fires when: **anomaly score > 0.65** AND **baseline is ready**.

---

## Running Integration Tests

With the server running in Terminal 1:

```bash
cd ai-self-healing-server
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