# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## First-time setup

After cloning, activate the pre-commit credential check:

```bash
git config core.hooksPath .githooks
```

The hook blocks commits containing hardcoded passwords, tokens, API keys, AWS keys, and private key headers. It allows `os.environ` and `os.getenv` reads through.

## Running tests

Activate the Linux venv first (created at `.venv-linux/`):

```bash
source .venv-linux/bin/activate
pytest test_rtl_433_pipeline.py -v          # all tests
pytest test_rtl_433_pipeline.py -v -k foo   # single test by name
```

The `.venv` directory is a Windows venv and does not work in Linux/WSL.

## Environment variables

Both `rtl_433_pipeline_dynatrace.py` and `sungrow_read.py` use the same env vars:

| Variable | Required | Default |
|---|---|---|
| `DT_API_TOKEN` | Yes for `rtl_433_pipeline_dynatrace.py` (exits on startup if unset); optional for `sungrow_read.py` (skips ingest if unset) | — |
| `DT_METRIC_INGEST_URL` | No | Hardcoded Dynatrace sprint URL in source |

## Testing rtl_433 pipeline on the Pi

The Pi is at `pi@10.0.0.3`. SSH requires the key at `~/.ssh/raspi_id_rsa`.

**Copy and run a test version without replacing the running script:**

```bash
scp -i ~/.ssh/raspi_id_rsa rtl_433_pipeline_dynatrace.py pi@10.0.0.3:/home/pi/rtl_433_pipeline_dynatrace_v2.py
ssh -i ~/.ssh/raspi_id_rsa pi@10.0.0.3 \
  "timeout 90 bash -c 'DT_API_TOKEN=<token> python3 /home/pi/rtl_433_pipeline_dynatrace_v2.py'"
```

The token value is in the existing `/home/pi/rtl_433_pipeline_dynatrace.py` on the Pi (`dtToken = "..."`).

**The RTL-SDR USB dongle can only be claimed by one process at a time.** If the original script is running, the new instance will start but `rtl_433` will fail with `usb_claim_interface error -6` and produce only empty lines (logged as `Invalid JSON: `). To test with live sensor data, stop the old process first:

```bash
ssh -i ~/.ssh/raspi_id_rsa pi@10.0.0.3 "pkill -f rtl_433_pipeline_dynatrace.py"
```

Restart the original afterwards:

```bash
ssh -i ~/.ssh/raspi_id_rsa pi@10.0.0.3 \
  "nohup bash -c 'DT_API_TOKEN=<token> python3 /home/pi/rtl_433_pipeline_dynatrace.py >> /home/pi/rtl_433_pipeline_dynatrace.log 2>&1' &"
```

The token value is in `/etc/otelcol-contrib/otelcol-contrib.conf` on the Pi (`DT_API_TOKEN=...`).

Live log on the Pi: `/home/pi/rtl_433_pipeline_dynatrace.log`

**Auto-start on reboot:** the Pi's crontab (`crontab -e` as the `pi` user) has an `@reboot` entry that starts the pipeline automatically:

```
@reboot DT_API_TOKEN=<token> python3 /home/pi/rtl_433_pipeline_dynatrace.py > /home/pi/rtl_433_pipeline_dynatrace.log 2>&1
```

The `>` truncates the log on each reboot (intentional — gives a clean log per boot session).

## OTel host metrics (Pi → Dynatrace)

The Pi runs `otelcol-contrib` as a systemd service to ship host metrics (CPU load, memory, disk, filesystem, network) to Dynatrace alongside the sensor pipeline. The two processes are completely independent.

### Installation (one-time)

The Pi is 32-bit ARMv7 (Raspbian 10). OneAgent does not support this architecture; the OTel collector does.

```bash
# Download and install the armv7 .deb (check https://github.com/open-telemetry/opentelemetry-collector-releases/releases for latest)
wget https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v0.152.0/otelcol-contrib_0.152.0_linux_armv7.deb -O /tmp/otelcol-contrib.deb
sudo dpkg -i /tmp/otelcol-contrib.deb
```

The `.deb` creates the systemd service and config directory automatically.

### Config files on the Pi

| File | Purpose |
|---|---|
| `/etc/otelcol-contrib/config.yaml` | Collector pipeline config — copy of `otel/config.yaml` in this repo |
| `/etc/otelcol-contrib/otelcol-contrib.conf` | Systemd env file — holds `DT_API_TOKEN` and `OTELCOL_OPTIONS` |

The env file is not in version control (contains the token). Its contents:

```
OTELCOL_OPTIONS="--config=/etc/otelcol-contrib/config.yaml"
DT_API_TOKEN=<token>
```

Permissions on the env file: `root:otelcol-contrib 640`.

### Deploying a config change

```bash
scp -i ~/.ssh/raspi_id_rsa otel/config.yaml pi@10.0.0.3:/tmp/otelcol-contrib-config.yaml
ssh -i ~/.ssh/raspi_id_rsa pi@10.0.0.3 \
  "sudo cp /tmp/otelcol-contrib-config.yaml /etc/otelcol-contrib/config.yaml && sudo systemctl restart otelcol-contrib"
```

### Service management

The service is enabled at boot via systemd (the `.deb` installer created the symlink automatically):

```
/etc/systemd/system/multi-user.target.wants/otelcol-contrib.service
```

No crontab entry is needed. On reboot the service starts automatically, picks up `DT_API_TOKEN` from `/etc/otelcol-contrib/otelcol-contrib.conf`, and begins shipping metrics within a few seconds.

```bash
# Status / logs
ssh -i ~/.ssh/raspi_id_rsa pi@10.0.0.3 "sudo systemctl status otelcol-contrib"
ssh -i ~/.ssh/raspi_id_rsa pi@10.0.0.3 "sudo journalctl -u otelcol-contrib -n 50 --no-pager"
```

### Metrics collected

Collection interval: 60 s. Metrics land in the Dynatrace sprint environment under the `opentelemetry` source.

| Metric | Notes |
|---|---|
| `pi.cpu.load_1m` / `pi.cpu.load_5m` / `pi.cpu.load_15m` | Renamed from `system.cpu.load_average.*` — Dynatrace adds quotes around digit-starting suffixes (`"1m"`) which DQL cannot reference |
| `system.memory.usage` | Gauge, split by `state` (used / free / cached / buffered / slab_*) |
| `system.filesystem.usage` | Gauge, split by `mountpoint` and `state` (used / free / reserved) |
| `system.disk.pending_operations` | Gauge |
| `system.network.connections` | Gauge |

**Known limitation:** monotonic cumulative sum metrics (`system.cpu.time`, `system.disk.io`, `system.network.io`, `system.network.dropped`) are rejected by the Dynatrace OTLP endpoint with `UNSUPPORTED_METRIC_TYPE_MONOTONIC_CUMULATIVE_SUM`. Adding a `cumulativetodelta` processor would fix this but has not been done yet.

### Dynatrace dashboard

The "Raspberry metrics" dashboard (`7f16613e-5aff-4a73-b448-ff66b7758efb`) shows all sensor and host metrics. Row 1: temperature and humidity. Row 2: CPU load average (line chart), memory % used (single value), disk % used (single value).

See the separate "Sungrow metrics" dashboard for inverter metrics (`751301dc-6ef3-4af6-a9a0-efc7131d5ba1`).

## Sungrow inverter reader (sungrow_read.py)

`sungrow_read.py` is a one-shot script that reads Sungrow SH6.0RT hybrid inverter registers via Modbus TCP, writes JSON to stdout, and optionally sends metrics to Dynatrace. It is driven entirely by cron — no daemon, no background threads.

### Technology

- **Protocol**: Modbus TCP (function code 4 — input registers)
- **Library**: `pymodbus` (v2.x and v3.x both supported; `unit=` vs `slave=` kwarg detected at import time)
- **Register map source**: mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant
- **Addressing**: 0-based (pymodbus address = register number − 1)
- **Word order**: 32-bit registers use little-endian word order (low word at lower address)

### Registers read

| Metric key | Register | Type | Scale | Unit | Notes |
|---|---|---|---|---|---|
| `sungrow_inverter_temperature` | 5008 (addr 5007) | int16 | ×0.1 | °C | |
| `sungrow_total_dc_power` | 5017–5018 (addr 5016) | uint32 | ×1 | W | PV yield |
| `sungrow_meter_active_power` | 5601–5602 (addr 5600) | int32 | ×1 | W | +ve = export (Einspeisung), −ve = import (Netzbezug) |
| `sungrow_load_power` | 13008–13009 (addr 13007) | int32 | ×1 | W | House consumption (Gesamtverbrauch) |
| `sungrow_battery_power` | 13022 (addr 13021) | int16 | ×1 | W | +ve = discharging, −ve = charging |
| `sungrow_battery_level` | 13023 (addr 13022) | uint16 | ×0.1 | % | State of charge |

Reads are batched into three blocks to avoid Modbus IllegalAddress errors:
- `fc=4, addr=4999, count=64` (covers inverter temp + DC power)
- `fc=4, addr=5600, count=2` (meter active power)
- `fc=4, addr=13000, count=25` (load power + battery)

The 5200 register range (alternative battery power) is inaccessible on this device and was not used.

### Running manually on the Pi

```bash
# With Dynatrace ingest
ssh -i ~/.ssh/raspi_id_rsa pi@10.0.0.3 \
  "DT_API_TOKEN=<token> python3 /home/pi/sungrow_read.py"

# JSON only (no ingest)
ssh -i ~/.ssh/raspi_id_rsa pi@10.0.0.3 "python3 /home/pi/sungrow_read.py"
```

The token is in `/etc/otelcol-contrib/otelcol-contrib.conf` on the Pi (`DT_API_TOKEN=...`).

### Deploying an update to the Pi

```bash
scp -i ~/.ssh/raspi_id_rsa sungrow_read.py pi@10.0.0.3:/home/pi/sungrow_read.py
```

No restart needed — cron picks up the new file on the next minute tick.

### Auto-start and scheduling

The Pi's crontab has two entries for the Sungrow reader:

```
@reboot sleep 30 && DT_API_TOKEN=<token> python3 /home/pi/sungrow_read.py > /home/pi/sungrow_read.log 2>&1
* * * * * DT_API_TOKEN=<token> python3 /home/pi/sungrow_read.py >> /home/pi/sungrow_read.log 2>&1
```

- `@reboot sleep 30`: waits 30 s for the network to come up (the inverter at 192.168.86.26 requires network, unlike the USB-based rtl_433)
- `@reboot` uses `>` to truncate the log on each boot; `* * * * *` uses `>>` to append during the day
- Log file: `/home/pi/sungrow_read.log`

### Dynatrace metrics and dashboard

Metrics are ingested under the `sungrow_` prefix with a `device=<host>` dimension. Unit and display name metadata is set once via the Settings API (`builtin:metric.metadata`; scope format `metric-<key>`).

The "Sungrow metrics" dashboard (`751301dc-6ef3-4af6-a9a0-efc7131d5ba1`) shows:
- Row 1 (full width): Area chart — PV yield, grid (±), battery (±), house load over 24 h
- Row 2: Battery SOC line chart, current battery level (single value), current inverter temperature (single value)

## Architecture

### rtl_433 pipeline (`rtl_433_pipeline_dynatrace.py`)

`rtl_433_pipeline_dynatrace.py` is a long-running daemon with three concurrent layers:

1. **Main thread** — spawns `rtl_433 -F json` as a subprocess, reads its stdout line-by-line, parses JSON, builds Dynatrace metric ingest lines, and pushes them into a bounded `queue.Queue(1000)`.
2. **Scheduler thread** (`ScheduleThread` / `run_continuously`) — runs `schedule` every minute, calling `schedulerJob()` which drains the queue and calls `send_metric_ingest`.
3. **`send_metric_ingest`** — POSTs the metric lines to Dynatrace using the [metric ingestion protocol](https://www.dynatrace.com/support/help/how-to-use-dynatrace/metrics/metric-ingestion/metric-ingestion-protocol/) (plain text, one line per metric, `key,tag=val value timestamp_ms`).

Two sensor types are handled in the main parsing block:
- **Temperature/humidity** (`temperature_C` + `humidity` fields) → `thermometer.temperature` and `thermometer.humidity` metrics.
- **IR motion sensor** (`state` + `unit` + `group` fields) → `infraredsensor.detectionstatus` metric (ON=1, OFF=0).

### Sungrow reader (`sungrow_read.py`)

`sungrow_read.py` is a simple one-shot script: connect → read three Modbus register blocks → decode → print JSON → POST to Dynatrace → exit. No threads, no queue. Scheduled by cron every minute.

## Test structure

`test_rtl_433_pipeline.py` tests against the module directly (`import rtl_433_pipeline_dynatrace as pipeline`). Because the sensor parsing logic lives inside `if __name__ == '__main__':`, the test file contains a `_parse_sensor()` helper that mirrors that logic — tests for metric line format call this helper rather than the module. Tests for `send_metric_ingest` and `schedulerJob` call the module functions directly using `unittest.mock`.

`TestKnownBugs` contains `test_queue_full_silently_drops_metric` which intentionally documents still-unfixed behaviour (silent drop when queue is full, bug #7 in `docs/rtl_433_pipeline_analysis.md`). This test will need updating when that bug is fixed.
