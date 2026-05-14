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

The pipeline requires one env var at runtime:

| Variable | Required | Default |
|---|---|---|
| `DT_API_TOKEN` | Yes — exits on startup if unset | — |
| `DT_METRIC_INGEST_URL` | No | Hardcoded Dynatrace sprint URL in source |

## Testing on the Raspberry Pi

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
  "nohup python3 /home/pi/rtl_433_pipeline_dynatrace.py >> /home/pi/rtl_433_pipeline_dynatrace.log 2>&1 &"
```

Live log on the Pi: `/home/pi/rtl_433_pipeline_dynatrace.log`

## Architecture

`rtl_433_pipeline_dynatrace.py` is a single-file pipeline with three concurrent layers:

1. **Main thread** — spawns `rtl_433 -F json` as a subprocess, reads its stdout line-by-line, parses JSON, builds Dynatrace metric ingest lines, and pushes them into a bounded `queue.Queue(1000)`.
2. **Scheduler thread** (`ScheduleThread` / `run_continuously`) — runs `schedule` every minute, calling `schedulerJob()` which drains the queue and calls `send_metric_ingest`.
3. **`send_metric_ingest`** — POSTs the metric lines to Dynatrace using the [metric ingestion protocol](https://www.dynatrace.com/support/help/how-to-use-dynatrace/metrics/metric-ingestion/metric-ingestion-protocol/) (plain text, one line per metric, `key,tag=val value timestamp_ms`).

Two sensor types are handled in the main parsing block:
- **Temperature/humidity** (`temperature_C` + `humidity` fields) → `thermometer.temperature` and `thermometer.humidity` metrics.
- **IR motion sensor** (`state` + `unit` + `group` fields) → `infraredsensor.detectionstatus` metric (ON=1, OFF=0).

## Test structure

`test_rtl_433_pipeline.py` tests against the module directly (`import rtl_433_pipeline_dynatrace as pipeline`). Because the sensor parsing logic lives inside `if __name__ == '__main__':`, the test file contains a `_parse_sensor()` helper that mirrors that logic — tests for metric line format call this helper rather than the module. Tests for `send_metric_ingest` and `schedulerJob` call the module functions directly using `unittest.mock`.

`TestKnownBugs` contains `test_queue_full_silently_drops_metric` which intentionally documents still-unfixed behaviour (silent drop when queue is full, bug #7 in `docs/rtl_433_pipeline_analysis.md`). This test will need updating when that bug is fixed.
