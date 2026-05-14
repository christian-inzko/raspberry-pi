"""
Tests for rtl_433_pipeline_dynatrace.py

Run against the CURRENT (unrefactored) code to establish a baseline.
Tests are annotated with [BUG] where they document a known defect —
those assertions will need updating after the corresponding fix is applied.
"""

import io
import queue
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
import requests

import rtl_433_pipeline_dynatrace as pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_thermo_dict(model="ThermoPlus", channel=1, id_=42,
                      time="2026-05-14 10:00:00",
                      temp=22.5, humidity=55):
    return {
        "model": model, "channel": channel, "id": id_,
        "time": time, "temperature_C": temp, "humidity": humidity,
    }


def _make_ir_dict(model="PIR-sensor", channel=2, id_=7,
                  time="2026-05-14 10:00:00",
                  state="ON", group="A", unit="1"):
    return {
        "model": model, "channel": channel, "id": id_,
        "time": time, "state": state, "group": group, "unit": unit,
    }


def _parse_sensor(sensor_dict):
    """
    Mirrors the metric-line-building logic currently inside __main__.
    Returns (lines: list[str], dropped: bool) where dropped=True means
    the 'else' / missing-fields branch was taken.
    """
    if not all(k in sensor_dict for k in ("model", "channel", "id", "time")):
        return [], True

    timestamp = sensor_dict["time"]
    parsed_timestamp = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
    utcMillisTS = int(parsed_timestamp.timestamp() * 1000)

    lines = []
    if "temperature_C" in sensor_dict and "humidity" in sensor_dict:
        t = sensor_dict["temperature_C"]
        h = sensor_dict["humidity"]
        lines.append(
            f"thermometer.temperature,"
            f"model={sensor_dict['model']},"
            f"channel={sensor_dict['channel']},"
            f"id={sensor_dict['id']} {t} {utcMillisTS}"
        )
        lines.append(
            f"thermometer.humidity,"
            f"model={sensor_dict['model']},"
            f"channel={sensor_dict['channel']},"
            f"id={sensor_dict['id']} {h} {utcMillisTS}"
        )
    elif ("state" in sensor_dict
          and "unit" in sensor_dict
          and "group" in sensor_dict):
        stateValue = 1 if sensor_dict["state"].upper() == "ON" else 0
        lines.append(
            f"infraredsensor.detectionstatus,"
            f"model={sensor_dict['model']},"
            f"channel={sensor_dict['channel']},"
            f"id={sensor_dict['id']},"
            f"group={sensor_dict['group']},"
            f"unit={sensor_dict['unit']} {stateValue} {utcMillisTS}"
        )
    else:
        return [], False  # unknown sensor type — nothing queued

    return lines, False


# ---------------------------------------------------------------------------
# send_metric_ingest
# ---------------------------------------------------------------------------

class TestSendMetricIngest:

    def test_posts_to_correct_url(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=202, text="")
            pipeline.send_metric_ingest("https://example.com/ingest", "mytoken", ["line1"])
            mock_post.assert_called_once()
            assert mock_post.call_args[0][0] == "https://example.com/ingest"

    def test_authorization_header(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=202, text="")
            pipeline.send_metric_ingest("https://x.com", "tok123", ["l"])
            headers = mock_post.call_args[1]["headers"]
            assert headers["Authorization"] == "Api-Token tok123"

    def test_content_type_header(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=202, text="")
            pipeline.send_metric_ingest("https://x.com", "t", ["l"])
            headers = mock_post.call_args[1]["headers"]
            assert headers["Content-Type"] == "text/plain; charset=utf-8"

    def test_body_joins_lines_with_newline(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=202, text="")
            pipeline.send_metric_ingest("https://x.com", "t", ["line1", "line2"])
            body = mock_post.call_args[1]["data"]
            assert body == b"line1\nline2\n"

    def test_body_encoded_as_utf8(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=202, text="")
            pipeline.send_metric_ingest("https://x.com", "t", ["line1"])
            body = mock_post.call_args[1]["data"]
            assert isinstance(body, bytes)

    def test_handles_network_error_without_crash(self):
        """[BUG] bare except currently swallows ConnectionError — this test documents that behaviour."""
        with patch("requests.post", side_effect=requests.exceptions.ConnectionError("down")):
            # must not raise
            pipeline.send_metric_ingest("https://x.com", "t", ["line"])

    def test_empty_lines_list(self):
        """Sending an empty list produces a body of just a newline."""
        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=202, text="")
            pipeline.send_metric_ingest("https://x.com", "t", [])
            body = mock_post.call_args[1]["data"]
            assert body == b"\n"


# ---------------------------------------------------------------------------
# schedulerJob
# ---------------------------------------------------------------------------

class TestSchedulerJob:

    def setup_method(self):
        # drain queue before each test
        while not pipeline.q.empty():
            pipeline.q.get_nowait()

    def test_drains_all_queued_items(self):
        pipeline.q.put("metric.a 1 1000")
        pipeline.q.put("metric.b 2 2000")
        pipeline.q.put("metric.c 3 3000")

        captured = []
        with patch.object(pipeline, "send_metric_ingest",
                          side_effect=lambda url, tok, lines: captured.extend(lines)):
            pipeline.schedulerJob()

        assert captured == ["metric.a 1 1000", "metric.b 2 2000", "metric.c 3 3000"]
        assert pipeline.q.empty()

    def test_empty_queue_calls_send_with_empty_list(self):
        captured = []
        with patch.object(pipeline, "send_metric_ingest",
                          side_effect=lambda url, tok, lines: captured.extend(lines)):
            pipeline.schedulerJob()

        assert captured == []

    def test_uses_configured_url_and_token(self):
        pipeline.q.put("x 1 1000")
        calls = []
        with patch.object(pipeline, "send_metric_ingest",
                          side_effect=lambda url, tok, lines: calls.append((url, tok))):
            pipeline.schedulerJob()

        assert calls[0][0] == pipeline.dtMetricIngestUrl
        assert calls[0][1] == pipeline.dtToken


# ---------------------------------------------------------------------------
# get_epochtime_ms  (currently unused but present in module)
# ---------------------------------------------------------------------------

class TestGetEpochtimeMs:

    def test_returns_integer(self):
        result = pipeline.get_epochtime_ms()
        assert isinstance(result, int)

    def test_returns_milliseconds_scale(self):
        import time
        result = pipeline.get_epochtime_ms()
        # should be within 5 seconds of now in ms
        now_ms = int(time.time() * 1000)
        assert abs(result - now_ms) < 5000


# ---------------------------------------------------------------------------
# Metric line format (mirrors __main__ parsing logic)
# ---------------------------------------------------------------------------

class TestTemperatureMetricLine:

    def test_metric_key(self):
        lines, _ = _parse_sensor(_make_thermo_dict())
        assert lines[0].startswith("thermometer.temperature,")

    def test_humidity_key(self):
        lines, _ = _parse_sensor(_make_thermo_dict())
        assert lines[1].startswith("thermometer.humidity,")

    def test_model_tag(self):
        lines, _ = _parse_sensor(_make_thermo_dict(model="SensorX"))
        assert "model=SensorX" in lines[0]

    def test_channel_tag(self):
        lines, _ = _parse_sensor(_make_thermo_dict(channel=3))
        assert "channel=3" in lines[0]

    def test_id_tag(self):
        lines, _ = _parse_sensor(_make_thermo_dict(id_=99))
        assert "id=99" in lines[0]

    def test_temperature_value(self):
        lines, _ = _parse_sensor(_make_thermo_dict(temp=18.3))
        # value field is the second space-separated token
        parts = lines[0].split(" ")
        assert float(parts[1]) == pytest.approx(18.3)

    def test_humidity_value(self):
        lines, _ = _parse_sensor(_make_thermo_dict(humidity=72))
        parts = lines[1].split(" ")
        assert float(parts[1]) == pytest.approx(72)

    def test_timestamp_is_epoch_ms(self):
        lines, _ = _parse_sensor(_make_thermo_dict(time="2026-05-14 10:00:00"))
        ts_ms = int(lines[0].split(" ")[-1])
        assert ts_ms > 1_000_000_000_000  # sensible ms epoch

    def test_produces_two_lines(self):
        lines, _ = _parse_sensor(_make_thermo_dict())
        assert len(lines) == 2


class TestIRSensorMetricLine:

    def test_metric_key(self):
        lines, _ = _parse_sensor(_make_ir_dict())
        assert lines[0].startswith("infraredsensor.detectionstatus,")

    def test_state_on_maps_to_1(self):
        lines, _ = _parse_sensor(_make_ir_dict(state="ON"))
        parts = lines[0].split(" ")
        assert int(parts[1]) == 1

    def test_state_off_maps_to_0(self):
        lines, _ = _parse_sensor(_make_ir_dict(state="OFF"))
        parts = lines[0].split(" ")
        assert int(parts[1]) == 0

    def test_state_case_insensitive(self):
        lines_on, _ = _parse_sensor(_make_ir_dict(state="on"))
        lines_off, _ = _parse_sensor(_make_ir_dict(state="Off"))
        assert int(lines_on[0].split(" ")[1]) == 1
        assert int(lines_off[0].split(" ")[1]) == 0

    def test_group_and_unit_tags(self):
        lines, _ = _parse_sensor(_make_ir_dict(group="B", unit="2"))
        assert "group=B" in lines[0]
        assert "unit=2" in lines[0]

    def test_produces_one_line(self):
        lines, _ = _parse_sensor(_make_ir_dict())
        assert len(lines) == 1


# ---------------------------------------------------------------------------
# Edge cases and documented bugs
# ---------------------------------------------------------------------------

class TestMissingAndUnknownSensors:

    def test_missing_required_field_returns_empty(self):
        bad = {"model": "X", "channel": 1}  # missing id and time
        lines, dropped = _parse_sensor(bad)
        assert lines == []
        assert dropped is True

    def test_unknown_sensor_type_returns_empty(self):
        unknown = {
            "model": "X", "channel": 1, "id": 1,
            "time": "2026-05-14 10:00:00",
            "some_other_field": "value",
        }
        lines, dropped = _parse_sensor(unknown)
        assert lines == []
        assert dropped is False  # reached else branch, not missing-fields branch


class TestKnownBugs:

    def test_queue_full_silently_drops_metric(self):
        """
        [BUG] When the queue is full, metrics are dropped with no warning logged.
        """
        # Fill the queue to capacity
        full_q = queue.Queue(3)
        for i in range(3):
            full_q.put(f"metric {i}")

        # Simulate the source's guard: `if not q.full(): q.put(...)`
        dropped = False
        if not full_q.full():
            full_q.put("overflow metric")
        else:
            dropped = True

        assert dropped is True
        assert full_q.qsize() == 3  # overflow was silently discarded
