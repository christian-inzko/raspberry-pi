# Analysis & Refactoring Plan: rtl_433_pipeline_dynatrace.py

**Date:** 2026-05-14

## Overview

`rtl_433_pipeline_dynatrace.py` bridges an RTL-433 RF receiver (IoT wireless sensors) with Dynatrace's metric ingestion API. It reads JSON lines from an `rtl_433` subprocess, parses temperature/humidity and IR motion sensor data, buffers metrics in a thread-safe queue, and flushes them to Dynatrace every minute via a background scheduler thread.

The script is functional but has several bugs, a security issue, and reliability/code-quality concerns that should be addressed before it runs in any shared or production environment.

---

## Issues Found

| # | Severity | Description | Location |
|---|----------|-------------|----------|
| 1 | **Critical** | Hardcoded API token in source | line 16 |
| 2 | **Critical** | f-string formatting bugs — literal `{}` printed instead of content | lines 125, 127, 129 |
| 3 | **Critical** | Bare `except:` clause masks all errors including `KeyboardInterrupt` | line 68 |
| 4 | Bug | `print("Pipeline process finished")` outside `if __name__ == '__main__':` block | line 134 |
| 5 | Bug | `@classmethod` on `ScheduleThread.run` — semantically wrong | lines 42–43 |
| 6 | Bug | `rtl_433` subprocess never terminated on exit | line 80 |
| 7 | Bug | Silent metric drop when queue is full — no warning logged | lines 109–112, 121 |
| 8 | Bug | Duplicate print statements in `send_metric_ingest` | lines 56 and 65 |
| 9 | Bug | Non-2xx HTTP responses silently ignored | line 67 |
| 10 | Code Quality | Unused function `get_epochtime_ms()` | lines 73–74 |
| 11 | Code Quality | Queue drain uses fragile TOCTOU pattern (`while not q.empty()`) | lines 22–24 |
| 12 | Code Quality | Non-Pythonic camelCase variable naming | lines 14–16 |
| 13 | Code Quality | Multiple imports on one line | line 3 |
| 14 | Code Quality | Sensor timestamp parsed without timezone info | lines 99–100 |
| 15 | Code Quality | No logging framework — raw `print()` throughout | all |

---

## Detailed Issue Descriptions

### 1. Hardcoded API Token *(Critical)*

```python
dtToken = "VwZcp60DSFun_cYSDr_Gw"  # line 16
```

Token is committed in plain source text — exposed in version control and any process listing.

**Fix:** Read from environment variables; exit with a clear error if unset.

```python
import os
dt_token = os.environ["DT_API_TOKEN"]
dt_metric_ingest_url = os.environ.get("DT_METRIC_INGEST_URL", "<default_url>")
```

---

### 2. f-string Formatting Bugs *(Critical)*

Three print statements use `{}` as a placeholder without an f-prefix or `.format()` call:

```python
print("Unknown sensor type: {}", line)  # line 125
print("Wrong sensor type: {}", line)    # line 127
print("Invalid json: {}", line)         # line 129
```

Only the literal string `{}` is printed; the actual offending data is never shown.

**Fix:**

```python
print(f"Unknown sensor type: {line}")
print(f"Wrong sensor data fields: {line}")
print(f"Invalid JSON: {line}")
```

---

### 3. Bare `except:` Clause *(Critical)*

```python
except:                                   # line 68
    print("Unexpected error ...")
    print(sys.exc_info()[0])
```

Catches `KeyboardInterrupt`, `SystemExit`, and every other exception — masking real failures.

**Fix:**

```python
except requests.exceptions.RequestException as e:
    print(f"HTTP error sending metrics: {e}")
```

---

### 4. `print()` Outside `__main__` Block *(Bug)*

Line 134 — `print("Pipeline process finished")` — sits outside the `if __name__ == '__main__':` block and executes on every import of the module.

**Fix:** Indent it inside the block.

---

### 5. `@classmethod` on `ScheduleThread.run` *(Bug)*

`threading.Thread.start()` calls `run()` as an instance method. The `@classmethod` decorator works by accident here and is semantically incorrect.

**Fix:** Remove `@classmethod`; rename `cls` to `self`.

---

### 6. Subprocess Never Terminated *(Bug)*

The `rtl_433` subprocess (`proc`) is spawned but never explicitly terminated. On `KeyboardInterrupt` it is left running as an orphan.

**Fix:** Call `proc.terminate()` in the `KeyboardInterrupt` handler and add a `try/finally` block.

---

### 7. Silent Metric Drop *(Bug)*

When the queue is full, metrics are silently discarded with no log output. In production this creates invisible data gaps with no alerting.

**Fix:**

```python
if not q.full():
    q.put(metric_line)
else:
    print(f"WARNING: queue full, dropping metric: {metric_line}")
```

---

### 8. Duplicate Prints *(Bug)*

`send_metric_ingest` prints both the raw lines list (line 56) and the formatted body (line 65). The first is redundant and clutters logs.

**Fix:** Remove the print on line 56.

---

### 9. HTTP Errors Silently Ignored *(Bug)*

A 4xx or 5xx response from Dynatrace is never checked. Metrics appear to send successfully even when the API rejects them.

**Fix:** Add `response_metrics.raise_for_status()` after the POST call.

---

### 10. Unused Function *(Code Quality)*

`get_epochtime_ms()` is defined at lines 73–74 but never called anywhere.

**Fix:** Remove it, or replace the `datetime.strptime` approach with it.

---

### 11. Queue Drain TOCTOU Pattern *(Code Quality)*

`while not q.empty(): q.get()` has a time-of-check / time-of-use race — `q.get()` may block after `q.empty()` returns `False` under concurrent access.

**Fix:**

```python
while True:
    try:
        lines.append(q.get_nowait())
    except queue.Empty:
        break
```

---

### 12. Non-Pythonic Naming *(Code Quality)*

`dtMetricIngestUrl`, `dtToken`, `apiToken` use camelCase. PEP 8 requires snake_case for module-level variables.

**Fix:** Rename to `dt_metric_ingest_url`, `dt_token`, `api_token`.

---

### 13. Multi-Import on One Line *(Code Quality)*

```python
import requests, sys  # line 3
```

**Fix:** Split into two lines per PEP 8.

---

### 14. Timezone-Naive Timestamp *(Code Quality)*

RTL-433 outputs time in the device's local timezone without zone information. `datetime.strptime` produces a naive datetime; conversion to epoch milliseconds may be off by the local UTC offset.

**Fix:** Use `datetime.now(timezone.utc)` at capture time instead of parsing the sensor's `time` field.

---

### 15. No Logging Framework *(Code Quality)*

All output uses `print()`. In production there is no way to control verbosity, redirect to a file, or filter by severity without code changes.

**Fix:** Replace `print()` calls with the standard `logging` module.

---

## Refactoring Implementation Order

Each step is independent and can be applied separately.

1. **Security** — Replace hardcoded credentials with `os.environ` reads; exit with a clear error if `DT_API_TOKEN` is unset.
2. **Bug fix** — Fix all three f-string errors (lines 125, 127, 129).
3. **Exception handling** — Replace bare `except:` with `except requests.exceptions.RequestException as e:`.
4. **Structural fix** — Move `print("Pipeline process finished")` inside `if __name__ == '__main__':`.
5. **Class fix** — Remove `@classmethod` from `ScheduleThread.run`; rename `cls` to `self`.
6. **Process cleanup** — Add `proc.terminate()` in `KeyboardInterrupt` handler plus a `finally` block.
7. **Queue drop warning** — Log `WARNING` when a metric is dropped due to a full queue.
8. **Dedup print** — Remove the redundant print on line 56.
9. **HTTP error check** — Add `response_metrics.raise_for_status()` after the POST.
10. **Dead code** — Remove unused `get_epochtime_ms()`.
11. **Queue drain** — Replace `while not q.empty()` pattern with `get_nowait()` loop.
12. **Naming** — Rename variables to snake_case.
13. **Imports** — Split multi-import line.

---

## Test Coverage

A baseline test suite exists in [`test_rtl_433_pipeline.py`](../test_rtl_433_pipeline.py) — **31 tests, all passing** on the current code. Uses `pytest` and `unittest.mock`; no hardware or network required.

| Test Class | Tests | What it covers |
|---|---|---|
| `TestSendMetricIngest` | 7 | URL, headers, body encoding, network error handling, empty list |
| `TestSchedulerJob` | 3 | Queue draining, empty queue, correct URL/token forwarding |
| `TestGetEpochtimeMs` | 2 | Return type and millisecond scale |
| `TestTemperatureMetricLine` | 9 | Metric key, tags, value, timestamp, line count |
| `TestIRSensorMetricLine` | 6 | Metric key, ON/OFF mapping, case insensitivity, tags |
| `TestMissingAndUnknownSensors` | 2 | Missing fields skipped, unknown type handled |
| `TestKnownBugs` | 2 | Documents the `{}` f-string bug and silent queue-drop bug |

Two tests (`test_print_format_bug_unknown_sensor`, `test_queue_full_silently_drops_metric`) intentionally document current broken behaviour — they will need to be updated to assert the corrected behaviour after the corresponding fixes are applied.

### Post-refactor tests to add

- `DT_API_TOKEN` env var loaded correctly
- Missing env var causes `SystemExit`
- Fixed print format shows actual line content (not `{}`)
- Full queue logs a `WARNING`
- 4xx/5xx HTTP response is detected and logged

### Running the tests

```bash
pytest test_rtl_433_pipeline.py -v
```
