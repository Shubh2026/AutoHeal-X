# Demo Script — AI Self-Healing Virtual Server System

**Total time:** 60 seconds  
**Audience:** Hackathon judges  
**Setup required:** All 3 terminals already running, baseline learning complete

---

## Before the Demo (Setup — do this 5+ minutes early)

1. Start server: `cd server && docker compose up --build`
2. Open `dashboard/dashboard_real.html` in Chrome — position on the **right half** of your screen
3. Start guardian: `cd guardian && python guardian.py` — position terminal on the **left half**
4. Wait for guardian to print: `✓ Baseline ready — IsolationForest trained on 50 samples`
5. Arrange windows so judges can see both the dashboard AND the guardian terminal simultaneously

Your screen should look like this:
```
┌──────────────────────┬──────────────────────────────┐
│  Guardian terminal   │    Dashboard in browser       │
│                      │                               │
│  [GREEN lines]       │  ● SERVER ONLINE              │
│  TICK   51           │  CPU   4.2%  MEM  38.1%       │
│  CPU  4.2% ...       │  DISK 61.0%  NET  12.4 KB/s   │
│  SCORE 0.082 NORMAL  │  Score ring: 0.08             │
│  HB UP               │  Event log: HEARTBEAT         │
└──────────────────────┴──────────────────────────────┘
```

---

## The 60-Second Demo

---

### 0:00 — 0:10 | Open: What problem does this solve?

**Say:**
> "Every production server eventually fails — CPU spikes, memory leaks, disk fills up. Traditional monitoring just sends an alert and waits for a human to fix it. We built a system that detects failures and heals itself, automatically, in seconds."

**Point at:** The dashboard — meter cards showing normal green readings

**Judges see:** A live dashboard with real metrics from a running Docker container

---

### 0:10 — 0:20 | Show the 3-component architecture

**Say:**
> "Three components working together. First, a Flask server running inside Docker — it exposes real CPU, memory, disk, and network metrics. Second, this live dashboard reads those metrics every second and visualises them. Third — and this is the AI part — our Guardian process runs a trained IsolationForest model that scores every reading against a learned baseline of normal behaviour."

**Point at:** Guardian terminal — show the green TICK lines

**Judges see:** The guardian printing `SCORE 0.082  SEVERITY NORMAL  CLASS NORMAL` in green

---

### 0:20 — 0:30 | Inject chaos

**Say:**
> "Watch what happens when the server fails. I'm injecting a CPU spike directly into the container."

**Action:** In the dashboard, select **Multi-Vector** from the dropdown and click **INJECT**

*(If demoing from terminal: `curl -X POST "http://localhost:5000/chaos?type=cpu_spike"`)*

**Judges see:**
- Dashboard: CPU meter turns **RED**, anomaly score ring starts climbing
- Guardian terminal: turns **YELLOW** then **RED** as score rises

---

### 0:30 — 0:45 | AI detects and responds

**Say:**
> "The IsolationForest model — trained on 50 samples of this server's normal behaviour — immediately scores this as anomalous. The score crosses our 0.65 threshold in under 10 seconds. The Guardian classifies the fault as CPU_OVERLOAD and fires the recovery action: kill the heaviest process inside the container."

**Point at:** Guardian terminal — show the RED recovery line

**Judges see on the guardian terminal:**
```
── TICK   56  10:24:15 ───────────────────────────────────────────────
  CPU  94.3%   MEM   38.4%   DISK  61.0%   NET    14.2 KB/s   PROCS   91
  SCORE 0.871   SEVERITY CRITICAL    CLASS CPU_OVERLOAD
  ACTION → OK — killed PID 847 inside ai-server
  HB UP
```

**Point at:** Dashboard event log

**Judges see:** New entry: `CHAOS: CPU_SPIKE — score: 0.871 🔴`

---

### 0:45 — 0:55 | Recovery confirmed — system heals

**Say:**
> "Recovery confirmed. The CPU is already dropping back to normal. The system healed itself in under 30 seconds with no human intervention. The event log shows the full timeline — anomaly detected, fault classified, action taken, system stabilised."

**Point at:** Dashboard — CPU meter turning back to yellow/green

**Judges see:**
- CPU meter returning toward green
- Score ring dropping below 0.65
- Guardian prints green ticks again: `SCORE 0.12  SEVERITY NORMAL`

---

### 0:55 — 1:00 | Close: What makes this different

**Say:**
> "What makes this different from a rule-based system: the IsolationForest model adapts to each server's normal baseline. If your server normally runs at 70% CPU, that's normal for it — a spike to 95% is the anomaly. The system learns what normal looks like for your infrastructure, then heals deviations from it."

---

## If Something Goes Wrong

| Problem | Recovery |
|---|---|
| Guardian terminal not turning red | Check baseline is ready — `grep "Baseline ready" guardian/guardian.log` |
| Dashboard shows SERVER OFFLINE | Check Terminal 1 — `docker ps` |
| Chaos button does nothing | Check browser console — CORS issue — restart server with flask-cors installed |
| Score doesn't exceed 0.65 | Use `multi_vector` instead of `cpu_spike` — hits multiple metrics at once |
| Recovery action shows FAILED | Docker daemon not accessible — Docker Desktop must be running on the host |

---

## Fallback Demo (if live demo fails)

If technical issues occur, show the pre-recorded terminal output:

```
── TICK   51  10:24:00 ──────────────────────────────────── BASELINE READY ──
  CPU   4.2%   MEM   38.1%   DISK  61.0%   NET    12.4 KB/s   PROCS   87
  SCORE 0.082   SEVERITY NORMAL     CLASS NORMAL
  HB UP

── TICK   52  10:24:05 ─────────────────────────────────────────────────────
  CPU  47.3%   MEM   39.2%   DISK  61.1%   NET    18.1 KB/s   PROCS   89
  SCORE 0.412   SEVERITY WARNING    CLASS ANOMALY_DETECTED
  HB UP

── TICK   53  10:24:10 ─────────────────────────────────────────────────────
  CPU  94.3%   MEM   38.4%   DISK  61.1%   NET    14.2 KB/s   PROCS   91
  SCORE 0.871   SEVERITY CRITICAL   CLASS CPU_OVERLOAD
  ACTION → OK — killed PID 847 inside ai-server
  HB UP

── TICK   54  10:24:15 ─────────────────────────────────────────────────────
  CPU  12.1%   MEM   38.2%   DISK  61.0%   NET    12.8 KB/s   PROCS   88
  SCORE 0.124   SEVERITY NORMAL     CLASS NORMAL
  HB UP
```

Walk judges through this output, explaining each line.

---

## Key Talking Points for Q&A

**"How is this different from Nagios / PagerDuty?"**
> Those tools alert humans. This system takes action autonomously. No alert, no ticket, no on-call engineer woken at 3am — the container heals itself.

**"What is IsolationForest actually doing?"**
> It's an unsupervised ML algorithm that learns what 'normal' looks like from 50 baseline samples. It randomly partitions the feature space — anomalies (unusual combinations of CPU/memory/disk/network) are easier to isolate and get lower scores. We convert those scores to a 0–1 range using a sigmoid calibrated to the training distribution.

**"What if the baseline is wrong?"**
> Restart guardian — it relearns from scratch. The 50-sample window (≈4 minutes of stable operation) ensures the baseline captures real normal behaviour. We also add a 30-second cooldown between recovery actions to prevent feedback loops.

**"Does it work on real infrastructure?"**
> Yes — the Docker SDK calls are real. In production you'd point CONTAINER_NAME at your actual service container. The recovery actions (process kill, log cleanup, container restart) are real Docker exec and restart operations, not simulations.