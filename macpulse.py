#!/usr/bin/env python3
"""MacPulse - macOS server monitoring with iMessage alerts."""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = SCRIPT_DIR / "settings.json"
STATE_PATH = Path.home() / ".macpulse_state.json"
LOG_PATH = Path.home() / "Library" / "Logs" / "macpulse.log"

logger = logging.getLogger("macpulse")


def setup_logging():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


DEFAULT_SETTINGS = {
    "recipient": "",
    "thresholds": {
        "cpu_percent": 90,
        "memory_percent": 85,
        "disk_percent": 80,
        "battery_below": 20,
        "temperature_c": 90,
    },
    "cooldown_minutes": 30,
    "checks": {
        "cpu": True,
        "memory": True,
        "disk": True,
        "temperature": True,
        "battery": True,
    },
}


def load_settings():
    if not SETTINGS_PATH.exists():
        with open(SETTINGS_PATH, "w") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=4)
        print(f"Created default settings file: {SETTINGS_PATH}")
        print("Edit 'recipient' in settings.json to enable iMessage alerts.")
    with open(SETTINGS_PATH) as f:
        return json.load(f)


def load_state():
    if STATE_PATH.exists():
        with open(STATE_PATH) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f)


# ── Metric collectors ────────────────────────────────────────────────


def get_cpu_usage():
    """Get CPU usage percentage from top."""
    out = subprocess.run(
        ["top", "-l", "1", "-n", "0", "-stats", "cpu"],
        capture_output=True, text=True, timeout=10,
    )
    for line in out.stdout.splitlines():
        if "CPU usage" in line:
            m = re.search(r"(\d+\.?\d*)% user.*?(\d+\.?\d*)% sys", line)
            if m:
                return round(float(m.group(1)) + float(m.group(2)), 1)
    return None


def get_memory_usage():
    """Get memory usage percentage from vm_stat and sysctl."""
    # Total physical memory
    out = subprocess.run(
        ["sysctl", "-n", "hw.memsize"],
        capture_output=True, text=True, timeout=5,
    )
    total_bytes = int(out.stdout.strip())

    # vm_stat for page statistics
    out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
    page_size = 16384  # default
    m = re.search(r"page size of (\d+) bytes", out.stdout)
    if m:
        page_size = int(m.group(1))

    pages = {}
    for line in out.stdout.splitlines():
        m = re.match(r"(.+?):\s+(\d+)", line)
        if m:
            pages[m.group(1).strip()] = int(m.group(2))

    free = pages.get("Pages free", 0)
    inactive = pages.get("Pages inactive", 0)
    speculative = pages.get("Pages speculative", 0)
    available = (free + inactive + speculative) * page_size
    used_pct = round((1 - available / total_bytes) * 100, 1)
    return used_pct


def get_disk_usage():
    """Get root disk usage percentage."""
    usage = shutil.disk_usage("/")
    return round(usage.used / usage.total * 100, 1)


def get_cpu_temperature():
    """Get CPU temperature via powermetrics or osx-cpu-temp."""
    # Try osx-cpu-temp first (no sudo needed)
    for cmd in ["osx-cpu-temp", "/usr/local/bin/osx-cpu-temp"]:
        try:
            out = subprocess.run(
                [cmd], capture_output=True, text=True, timeout=5,
            )
            if out.returncode == 0:
                m = re.search(r"(\d+\.?\d*)\s*°?C", out.stdout)
                if m:
                    return float(m.group(1))
        except FileNotFoundError:
            continue

    # Try powermetrics (needs sudo / root)
    try:
        out = subprocess.run(
            ["sudo", "-n", "powermetrics", "--samplers", "smc", "-i", "1", "-n", "1"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            m = re.search(r"CPU die temperature:\s*(\d+\.?\d*)\s*C", out.stdout)
            if m:
                return float(m.group(1))
    except FileNotFoundError:
        pass

    return None


def get_battery_info():
    """Get battery charge percentage and charging state from pmset."""
    out = subprocess.run(
        ["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5,
    )
    text = out.stdout
    m = re.search(r"(\d+)%;\s*(\w[\w\s]*?);", text)
    if m:
        return {"percent": int(m.group(1)), "state": m.group(2).strip()}
    return None


def get_uptime_and_load():
    """Get load averages."""
    load1, load5, load15 = os.getloadavg()
    return {"load_1m": round(load1, 2), "load_5m": round(load5, 2), "load_15m": round(load15, 2)}


# ── Alert logic ──────────────────────────────────────────────────────


def check_thresholds(metrics, thresholds, checks):
    """Return list of (metric_name, message) for exceeded thresholds."""
    alerts = []
    if checks.get("cpu") and metrics.get("cpu") is not None:
        if metrics["cpu"] >= thresholds["cpu_percent"]:
            alerts.append(("cpu", f"CPU usage at {metrics['cpu']}% (threshold: {thresholds['cpu_percent']}%)"))

    if checks.get("memory") and metrics.get("memory") is not None:
        if metrics["memory"] >= thresholds["memory_percent"]:
            alerts.append(("memory", f"Memory usage at {metrics['memory']}% (threshold: {thresholds['memory_percent']}%)"))

    if checks.get("disk") and metrics.get("disk") is not None:
        if metrics["disk"] >= thresholds["disk_percent"]:
            alerts.append(("disk", f"Disk usage at {metrics['disk']}% (threshold: {thresholds['disk_percent']}%)"))

    if checks.get("temperature") and metrics.get("temperature") is not None:
        if metrics["temperature"] >= thresholds["temperature_c"]:
            alerts.append(("temperature", f"CPU temp at {metrics['temperature']}°C (threshold: {thresholds['temperature_c']}°C)"))

    if checks.get("battery") and metrics.get("battery") is not None:
        batt = metrics["battery"]
        if batt["state"] != "charging" and batt["percent"] <= thresholds["battery_below"]:
            alerts.append(("battery", f"Battery at {batt['percent']}% (threshold: {thresholds['battery_below']}%)"))

    return alerts


def filter_by_cooldown(alerts, state, cooldown_minutes):
    """Remove alerts that are still within their cooldown window."""
    now = time.time()
    filtered = []
    for metric, msg in alerts:
        last = state.get(metric, 0)
        if now - last >= cooldown_minutes * 60:
            filtered.append((metric, msg))
    return filtered


def send_imessage(recipient, message):
    """Send an iMessage via osascript."""
    escaped = message.replace("\\", "\\\\").replace('"', '\\"')
    script = f'tell application "Messages" to send "{escaped}" to buddy "{recipient}"'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        logger.error("Failed to send iMessage: %s", result.stderr.strip())
        return False
    return True


# ── Main ─────────────────────────────────────────────────────────────


def collect_metrics(checks):
    """Collect all enabled metrics."""
    metrics = {}
    if checks.get("cpu"):
        metrics["cpu"] = get_cpu_usage()
    if checks.get("memory"):
        metrics["memory"] = get_memory_usage()
    if checks.get("disk"):
        metrics["disk"] = get_disk_usage()
    if checks.get("temperature"):
        metrics["temperature"] = get_cpu_temperature()
    if checks.get("battery"):
        metrics["battery"] = get_battery_info()
    metrics["load"] = get_uptime_and_load()
    return metrics


def print_metrics(metrics):
    """Print current metrics to stdout."""
    print("MacPulse - System Metrics")
    print("=" * 40)
    if "cpu" in metrics:
        val = metrics["cpu"]
        print(f"  CPU Usage:     {val}%" if val is not None else "  CPU Usage:     unavailable")
    if "memory" in metrics:
        val = metrics["memory"]
        print(f"  Memory Usage:  {val}%" if val is not None else "  Memory Usage:  unavailable")
    if "disk" in metrics:
        print(f"  Disk Usage:    {metrics['disk']}%")
    if "temperature" in metrics:
        val = metrics["temperature"]
        print(f"  CPU Temp:      {val}°C" if val is not None else "  CPU Temp:      unavailable")
    if "battery" in metrics:
        batt = metrics["battery"]
        if batt:
            print(f"  Battery:       {batt['percent']}% ({batt['state']})")
        else:
            print("  Battery:       unavailable")
    load = metrics.get("load")
    if load:
        print(f"  Load Avg:      {load['load_1m']} / {load['load_5m']} / {load['load_15m']}")
    print("=" * 40)


def run_monitor():
    settings = load_settings()
    checks = settings.get("checks", {})
    thresholds = settings.get("thresholds", {})
    recipient = settings.get("recipient", "")
    cooldown = settings.get("cooldown_minutes", 30)

    metrics = collect_metrics(checks)
    print_metrics(metrics)
    logger.info("Metrics: %s", json.dumps({k: v for k, v in metrics.items()}, default=str))

    alerts = check_thresholds(metrics, thresholds, checks)
    if not alerts:
        logger.info("All metrics within thresholds.")
        return

    state = load_state()
    to_send = filter_by_cooldown(alerts, state, cooldown)
    if not to_send:
        logger.info("Alerts suppressed by cooldown: %s", [a[0] for a in alerts])
        return

    hostname = subprocess.run(
        ["hostname", "-s"], capture_output=True, text=True
    ).stdout.strip() or "mac-server"
    body = f"[MacPulse] {hostname}\n" + "\n".join(f"- {msg}" for _, msg in to_send)

    if not recipient:
        logger.warning("Alerts triggered but no recipient configured in settings.json")
        print(f"\nALERTS (no recipient configured):\n{body}")
        return

    if send_imessage(recipient, body):
        now = time.time()
        for metric, _ in to_send:
            state[metric] = now
        save_state(state)
        logger.info("Alert sent for: %s", [m for m, _ in to_send])
    else:
        print("Failed to send iMessage alert. Check logs for details.")


def test_alert():
    settings = load_settings()
    recipient = settings.get("recipient", "")
    if not recipient:
        print("Error: Set 'recipient' in settings.json before testing.")
        sys.exit(1)
    msg = "[MacPulse] Test alert — if you see this, iMessage alerts are working!"
    if send_imessage(recipient, msg):
        print("Test message sent successfully.")
    else:
        print("Failed to send test message. Check logs.")
        sys.exit(1)


def print_install():
    script = Path(__file__).resolve()
    python = sys.executable
    entry = f"*/5 * * * * {python} {script} >> /dev/null 2>&1"
    print("Add this to your crontab (crontab -e):\n")
    print(f"  {entry}")
    print("\nOr create a launchd plist for more reliable scheduling.")


def main():
    setup_logging()

    if "--test" in sys.argv:
        test_alert()
    elif "--install" in sys.argv:
        print_install()
    else:
        run_monitor()


if __name__ == "__main__":
    main()
