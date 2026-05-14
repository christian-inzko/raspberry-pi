# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
