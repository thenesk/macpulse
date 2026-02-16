# MacPulse

Lightweight macOS server monitoring script that sends iMessage alerts when system health thresholds are exceeded. Zero external dependencies — uses only Python stdlib and macOS built-in tools.

## Quick Start

```bash
# First run creates a default settings.json
python3 macpulse.py

# Edit settings with your iMessage recipient
# (phone number or Apple ID email)
nano settings.json

# Verify iMessage delivery
python3 macpulse.py --test

# Print a crontab entry for scheduled monitoring
python3 macpulse.py --install
```

## What It Monitors

| Metric | Source | Default Threshold |
|---|---|---|
| CPU usage | `top -l 1` | 90% |
| Memory pressure | `vm_stat` + `sysctl hw.memsize` | 85% |
| Disk usage | `shutil.disk_usage("/")` | 80% |
| CPU temperature | `osx-cpu-temp` or `sudo powermetrics` | 90°C |
| Battery charge | `pmset -g batt` | 20% |
| Load averages | `os.getloadavg()` | (display only) |

Temperature is skipped gracefully if neither `osx-cpu-temp` nor passwordless `sudo powermetrics` is available.

## Configuration

Edit `settings.json` (created automatically on first run):

```json
{
    "recipient": "+1234567890",
    "thresholds": {
        "cpu_percent": 90,
        "memory_percent": 85,
        "disk_percent": 80,
        "battery_below": 20,
        "temperature_c": 90
    },
    "cooldown_minutes": 30,
    "checks": {
        "cpu": true,
        "memory": true,
        "disk": true,
        "temperature": true,
        "battery": true
    }
}
```

| Field | Description |
|---|---|
| `recipient` | Phone number or Apple ID for iMessage alerts |
| `thresholds` | Per-metric alert thresholds |
| `cooldown_minutes` | Minimum time between repeated alerts for the same metric |
| `checks` | Toggle individual checks on/off |

## Scheduling

Run `python3 macpulse.py --install` to get a crontab entry, then add it with `crontab -e`. Default interval is every 5 minutes.

For more reliable scheduling on macOS, use a launchd plist instead of cron.

## Files

| Path | Purpose |
|---|---|
| `macpulse.py` | Main monitoring script |
| `settings.json` | User configuration (gitignored) |
| `~/.macpulse_state.json` | Alert cooldown state |
| `~/Library/Logs/macpulse.log` | Rotating log file (1 MB, 3 backups) |

## Requirements

- macOS
- Python 3.6+
- Messages.app signed in for iMessage delivery
- (Optional) [`osx-cpu-temp`](https://github.com/lavoiesl/osx-cpu-temp) for CPU temperature monitoring:
  ```bash
  brew install osx-cpu-temp
  ```
