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

`sungrow_read.py` additionally reads `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` (optional, defaults to `http://localhost:4318/v1/traces` — the local `otelcol-contrib` OTLP receiver). See "OTel tracing (sungrow_read.py)" below.

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
| `/etc/otelcol-contrib/otelcol-contrib.conf` | Systemd env file — holds `DT_API_TOKEN`, `HA_API_TOKEN`, and `OTELCOL_OPTIONS` |

The env file is not in version control (contains the tokens). Its contents:

```
OTELCOL_OPTIONS="--config=/etc/otelcol-contrib/config.yaml"
DT_API_TOKEN=<token>
HA_API_TOKEN=<home assistant long-lived access token>
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
| `process.cpu.time`, `process.memory.usage`, `process.disk.io`, etc. | Per-process metrics from the `process` scraper (added for issue #8 — no `OTEL_PROCESS` Smartscape entity existed before this, since the receiver never emitted process-level telemetry) |

`otelcol-contrib` runs under its own dedicated `otelcol-contrib` system user (not `root` or `pi`), and every process on the Pi is owned by `root` or `pi` — so the `process` scraper can never read `/proc/<pid>/exe` (needs ptrace access) or `/proc/<pid>/io` (needs same-owner or elevated privileges) for any process but itself. Confirmed by testing on the Pi: without muting, this floods the log every scrape (60s) with one giant "Error scraping metrics" line per process. `mute_process_exe_error`, `mute_process_io_error`, and `mute_process_user_error` are set to suppress this expected, permanent condition — the scraper still reports name, PID, CPU, and memory per process (read from `/proc/<pid>/stat` and `/proc/<pid>/status`, which are world-readable), just not executable path or disk I/O for processes it doesn't own. Granting the service account broader `/proc` access (e.g. `CAP_SYS_PTRACE`, or running as `root`) would fix the gap but widens what a compromised collector process could read from every other process on the box — not done without discussing the tradeoff first.

Monotonic cumulative sum metrics (`system.cpu.time`, `system.disk.io`, `system.disk.io_time`, `system.network.io`, `system.network.dropped`, `system.network.errors`, and others) are rejected outright by the Dynatrace OTLP endpoint (`UNSUPPORTED_METRIC_TYPE_MONOTONIC_CUMULATIVE_SUM`) unless converted first. The `cumulativetodelta` processor in the metrics pipeline runs with no `include` filter — it converts every cumulative sum metric globally — because the `host_metrics` receiver emits more of these than any static list reliably enumerates (three showed up during testing beyond the four originally identified).

A `resource` processor stamps `host.name: raspberrypi` on every metric so a Smartscape host entity forms and host-level anomaly detection is available.

### Logs (Pi → Dynatrace)

The `filelog` receiver tails `/home/pi/rtl_433_pipeline_dynatrace.log` and `/home/pi/sungrow_read.log` (`start_at: end`, so only new lines after collector startup are shipped) and forwards them through the `otlp_http` exporter's `logs_endpoint`. This surfaces pipeline errors (e.g. the `usb_claim_interface error -6` USB-conflict case documented above) without SSHing into the Pi.

`DT_API_TOKEN` needs the `logs.ingest` scope added (in addition to `metrics.ingest`) for this pipeline to authenticate — check the token's scopes in Dynatrace before deploying this config.

### Traces (Pi → Dynatrace)

The `otlp` receiver (`protocols.http`, bound to `127.0.0.1:4318`, local-only — no process outside the Pi can submit spans) accepts OTLP/HTTP spans from local scripts and forwards them through the `otlp_http` exporter's `traces_endpoint`. `sungrow_read.py` is currently the only emitter (see "OTel tracing" under the Sungrow reader section below).

This was added for issue #11 (Bluebox SRE investigation): the Bluebox setup page's telemetry check looks specifically for service-level data (a span with a `service.name` resource attribute, producing an `OTEL_SERVICE` entity) — host metrics and logs alone don't satisfy it, even though they prove the device is fully instrumented for its actual purpose. This minimal one-span-per-run addition to `sungrow_read.py` gives the check something to find without adding tracing to the whole codebase.

`DT_API_TOKEN` needs the `openTelemetryTrace.ingest` scope (in addition to `metrics.ingest` and `logs.ingest`) for this pipeline to authenticate.

### Home Assistant PV metrics receiver

The `prometheus/homeassistant` receiver in `otel/config.yaml` scrapes PV-system metrics from a second Raspberry Pi 4 (`192.168.86.211`) running Home Assistant, which itself reads a Sungrow SH8.0RT-20 inverter (WiNet-S dongle, `192.168.86.26`) over Modbus TCP. This is separate from and complementary to `sungrow_read.py` (which reads a different inverter directly via Modbus TCP and cron, see below) — this receiver instead scrapes HA's built-in Prometheus exporter for the daily/cumulative energy sensors HA already tracks.

The collector (running on `pi@10.0.0.3`, alongside `host_metrics`) reaches out over the LAN to `192.168.86.211:8123/api/prometheus` every 60 s, authenticating with a bearer token (`HA_API_TOKEN`).

**Known issue:** this scrape crosses onto the Pi's WiFi-connected subnet (`192.168.86.0/24`, separate from its primary `10.0.0.3` management network) to reach the HA Pi, and is prone to intermittent failures (`Failed to scrape Prometheus endpoint` in the collector logs) — a real scrape timeout, not a config bug, most likely from WiFi flakiness. The `scrape_timeout: 30s` setting (vs. the Prometheus default of 10s) mitigates this; if failures reappear at a meaningful rate, that's the first thing to check/increase.

**Prerequisites (manual, one-time, on the Home Assistant Pi — not this repo):**
- Enable the **Prometheus** integration/add-on in Home Assistant (Settings → Devices & Services → Add Integration → Prometheus).
- Create a **Long-Lived Access Token** for the collector (HA profile page → Long-Lived Access Tokens).
- Add the token as `HA_API_TOKEN=<token>` to `/etc/otelcol-contrib/otelcol-contrib.conf` on `pi@10.0.0.3` (same file/pattern as `DT_API_TOKEN`, see above).
- Deploy the updated `otel/config.yaml` (see "Deploying a config change" above) and restart `otelcol-contrib`.

**Metrics exposed by HA's Prometheus integration** (entity IDs on the HA Pi):

| Entity | Description | Unit |
|---|---|---|
| `sensor.daily_pv_generation` | Daily PV yield | kWh |
| `sensor.daily_exported_energy` | Daily grid export | kWh |
| `sensor.daily_imported_energy` | Daily grid import | kWh |
| `sensor.daily_battery_charge` | Daily battery charge | kWh |
| `sensor.daily_battery_discharge` | Daily battery discharge | kWh |
| `sensor.battery_level` | Battery state of charge | % |
| `sensor.total_dc_power` | Current PV power | W |
| `sensor.export_power` | Current grid export power | W |

HA's Prometheus exporter names metrics by domain/unit (e.g. `homeassistant_sensor_energy_kwh`) with the entity id as a label — check the actual metric/label names against a live scrape (`curl -H "Authorization: Bearer $HA_API_TOKEN" http://192.168.86.211:8123/api/prometheus`) before building Dynatrace dashboard queries, since HA does not use the literal entity names as metric names.

### Dynatrace dashboard

The "Raspberry metrics" dashboard (`7f16613e-5aff-4a73-b448-ff66b7758efb`) shows all sensor and host metrics. Row 1: temperature and humidity. Row 2: CPU usage %, memory usage %, disk usage % for `/` (all line charts; CPU = 1 − idle/total of `system.cpu.time`).

See the separate "Sungrow metrics" dashboard for inverter metrics (`751301dc-6ef3-4af6-a9a0-efc7131d5ba1`).

All three dashboards are versioned in `dynatrace/dashboards/*.yaml` (id, name, type, content). Deploy to a tenant with `dtctl apply -f dynatrace/dashboards/<name>.yaml` (or loop over the directory): on a new tenant the file's `id` doesn't exist yet so it is created with that same id, and re-running updates it in place. Switch tenant first with `dtctl config set-context` / `dtctl auth login`. After editing a dashboard in the UI, re-export with `dtctl get dashboard <id> -o json` and rewrite the YAML (keep only `id`, `name`, `type`, `content`).

The "Home Assistant Prometheus receiver metrics" dashboard (`9ee37c5d-d887-48d8-84b1-a94a21133447`) shows the `prometheus/homeassistant` metrics:
- HA process CPU (% of 4 cores = CPU seconds ÷ 60 s scrape interval ÷ 4) and memory (% of 4 GB) — process-level only, since HA's Prometheus export has no host CPU/memory sensors (would need the System Monitor integration).
- Sensor charts grouped by function and unit (grid/meter, PV/house, phases, limits, Shelly plugs, daily/total energy, battery, voltage, current, temperatures, network). Each tile filters out series that are constant in the selected timeframe (`arrayMax(v) != arrayMin(v)`); no separate "constants" tile because those series were 0 over 30 days.
- "Inverter state (decoded)" table: raw Sungrow codes decoded with the mkaiser register map (on/off 0xAA/0x55, running state, EMS mode, device type code, ...). The protocol version decoding (`V1.0.5.0`, raw bytes dotted) and the `0xFFFF` = "not available" reading for the APL registers are guesses — the register map doesn't document them.
- "Sensor Timestamp" table converts epoch seconds to real timestamps.

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

### OTel tracing

`sungrow_read.py` wraps its whole run in a single `sungrow_read.run` span (`service.name: sungrow-read`), exported via OTLP/HTTP to the local `otelcol-contrib` collector's `otlp` receiver (`http://localhost:4318/v1/traces` by default, overridable with `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`), which forwards it to Dynatrace. `requirements`: `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http` (in addition to `pymodbus`, `requests`).

This exists solely to give Bluebox's setup-page telemetry check a `service.name` to find (see "Traces (Pi → Dynatrace)" above, issue #11) — it is not needed for the script's own metrics/JSON-output purpose, so `rtl_433_pipeline_dynatrace.py` was deliberately left uninstrumented. The span export uses `BatchSpanProcessor`, so `main()` calls `force_flush()` in a `finally` block before the process exits — without it, the batch would still be queued in a background thread when the one-shot cron process ends and the span would be silently lost. If the local collector is unreachable, the SDK logs an export error internally but does not raise, so it can't break the script's other outputs (stdout JSON, metric ingest).

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

One-time on the Pi: install the OTel packages (`pip3 install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http`) and deploy the updated `otel/config.yaml` (see "Deploying a config change" above, needed for the new `otlp` receiver/`traces` pipeline) before the traced version will actually reach Dynatrace — otherwise the script still runs fine, it just logs a local export error each run (see "OTel tracing" above).

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
- Row 1 (full width): Line chart "Power flow (W)" — Solar production, House consumption, Grid power (+ export / − import), Battery power (+ charging / − discharging). The battery sign is flipped in the DQL (`` `Battery power`[] * -1 ``) because the raw `sungrow_battery_power` is +ve = discharging
- Row 2 (full width): Outdoor temperature (°C) & humidity (%) line chart (`AmbientWeather-TX8300`) — added 2026-07-21; an indoor series (`Hideki-TS04`) was on this tile too but was removed 2026-07-23 as out of place on an inverter-focused dashboard
- Row 3: Battery SOC line chart, current battery level (single value), current inverter temperature (single value)

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
