import os
import time
import threading
import psutil
import hashlib
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

START_TIME = time.time()
CHAOS_DURATION = 15


# -----------------------------
# Utility: Run task in background
# -----------------------------
def run_in_background(target):
    thread = threading.Thread(target=target)
    thread.daemon = True
    thread.start()


# -----------------------------
# Chaos Functions
# -----------------------------
def cpu_spike():
    """
    Burn CPU across cores using a pure-Python (no external binaries) worker model.

    Note: We intentionally use a C-backed hash primitive (pbkdf2_hmac) that releases
    the GIL so that multiple Python threads can utilize multiple CPU cores.
    """
    duration_s = CHAOS_DURATION
    deadline = time.time() + duration_s
    stop = threading.Event()

    cpu_count = os.cpu_count() or 1
    worker_count = max(1, cpu_count)

    # Parameters tuned to be "heavy" but not allocate unbounded memory.
    algo = "sha256"
    salt = b"autohealx-chaos-salt"
    iterations = 250_000

    def burn():
        # Keep locals for speed
        pbkdf2 = hashlib.pbkdf2_hmac
        password = b"autohealx-chaos-password"
        while not stop.is_set():
            # Repeatedly compute a CPU-heavy KDF; output discarded intentionally.
            pbkdf2(algo, password, salt, iterations)

    workers = []
    for _ in range(worker_count):
        t = threading.Thread(target=burn, daemon=True)
        t.start()
        workers.append(t)

    try:
        while time.time() < deadline:
            time.sleep(0.05)
    finally:
        stop.set()
        for t in workers:
            t.join(timeout=0.5)


def memory_leak():
    data = []
    end_time = time.time() + CHAOS_DURATION
    while time.time() < end_time:
        data.append(os.urandom(1024 * 1024))  # allocate 1MB chunks


def disk_flood():
    filepath = "/tmp/dummy_flood.bin"
    with open(filepath, "wb") as f:
        f.write(os.urandom(100 * 1024 * 1024))  # 100MB file
    time.sleep(CHAOS_DURATION)
    if os.path.exists(filepath):
        os.remove(filepath)


def traffic_spike():
    import requests

    def hit():
        try:
            requests.get("http://localhost:5000/health")
        except:
            pass

    end_time = time.time() + CHAOS_DURATION
    while time.time() < end_time:
        threads = []
        for _ in range(50):
            t = threading.Thread(target=hit)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()


def multi_vector():
    run_in_background(cpu_spike)
    run_in_background(memory_leak)
    run_in_background(traffic_spike)


# -----------------------------
# API Endpoints
# -----------------------------
@app.route("/metrics", methods=["GET"])
def metrics():
    cpu = psutil.cpu_percent(interval=0.2)
    memory = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent
    net = psutil.net_io_counters()
    processes = len(psutil.pids())

    return jsonify({
        "cpu_percent": cpu,
        "memory_percent": memory,
        "disk_percent": disk,
        "net_bytes_sent": net.bytes_sent,
        "net_bytes_recv": net.bytes_recv,
        "active_processes": processes
    })


@app.route("/health", methods=["GET"])
def health():
    uptime = int(time.time() - START_TIME)
    return jsonify({
        "status": "ok",
        "uptime_seconds": uptime
    })


@app.route("/chaos", methods=["POST"])
def chaos():
    chaos_type = request.args.get("type")

    if chaos_type == "cpu_spike":
        run_in_background(cpu_spike)
    elif chaos_type == "memory_leak":
        run_in_background(memory_leak)
    elif chaos_type == "disk_flood":
        run_in_background(disk_flood)
    elif chaos_type == "traffic_spike":
        run_in_background(traffic_spike)
    elif chaos_type == "multi_vector":
        run_in_background(multi_vector)
    else:
        return jsonify({"error": "Invalid chaos type"}), 400

    return jsonify({
        "status": "ok",
        "type": chaos_type,
        "duration": CHAOS_DURATION
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)