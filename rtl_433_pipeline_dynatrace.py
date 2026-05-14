import os
import sys
import json
import queue
import logging
import subprocess
import threading
import time
from datetime import datetime, timezone
from json import JSONDecodeError

import requests
import schedule

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

BUF_SIZE = 1000
q = queue.Queue(BUF_SIZE)

dt_metric_ingest_url = os.environ.get(
    "DT_METRIC_INGEST_URL",
    "https://rhp60717.sprint.dynatracelabs.com/api/v2/metrics/ingest"
)
dt_token = os.environ.get("DT_API_TOKEN")


def scheduler_job():
    timestamp = datetime.now()
    log.info(f"SchedulerJob running at {timestamp}")
    lines = []
    while True:
        try:
            lines.append(q.get_nowait())
        except queue.Empty:
            break
    send_metric_ingest(dt_metric_ingest_url, dt_token, lines)
    log.info(f"SchedulerJob finished at {timestamp} and has sent {len(lines)} lines")


def run_continuously(interval=1):
    """Continuously run, while executing pending jobs at each elapsed
    time interval.
    @return cease_continuous_run: threading.Event which can be set to
    cease continuous run.
    Please note that it is *intended behavior that run_continuously()
    does not run missed jobs*. For example, if you've registered a job
    that should run every minute and you set a continuous run interval
    of one hour then your job won't be run 60 times at each interval but
    only once.
    """
    cease_continuous_run = threading.Event()

    class ScheduleThread(threading.Thread):
        def run(self):
            while not cease_continuous_run.is_set():
                schedule.run_pending()
                time.sleep(interval)

    continuous_thread = ScheduleThread()
    continuous_thread.daemon = True
    continuous_thread.start()
    return cease_continuous_run


def send_metric_ingest(url, token, lines):
    # see https://www.dynatrace.com/support/help/dynatrace-api/environment-api/metric-v2/post-ingest-metrics/#example
    api_token = 'Api-Token ' + token
    body_metrics = '\n'.join(lines) + '\n'
    headers = {
        'Authorization': api_token,
        'Content-Type': 'text/plain; charset=utf-8'
    }
    try:
        log.info(f'Sending lines for metric ingest: {body_metrics}')
        response_metrics = requests.post(url, headers=headers, data=body_metrics.encode('utf-8'))
        response_metrics.raise_for_status()
        log.info(f'metrics response status: {response_metrics.status_code},  data: {response_metrics.text}')
    except requests.exceptions.RequestException as e:
        log.error(f"HTTP error sending metric ingest lines: {e}")


def _put_metric(metric_line):
    if not q.full():
        q.put(metric_line)
    else:
        log.warning(f"Queue full, dropping metric: {metric_line}")


if __name__ == '__main__':
    if not dt_token:
        log.error("DT_API_TOKEN environment variable is not set.")
        sys.exit(1)
    log.info("Pipeline process started")
    proc = subprocess.Popen(
        ["/usr/local/bin/rtl_433", "-F", "json"],
        stdout=subprocess.PIPE,
        universal_newlines=True
    )

    schedule.every(1).minutes.do(scheduler_job)
    cease_continuous_run = run_continuously()

    try:
        while True:
            line = proc.stdout.readline()
            try:
                sensor_dict = json.loads(line)
                # see metric ingest protocol: https://www.dynatrace.com/support/help/how-to-use-dynatrace/metrics/metric-ingestion/metric-ingestion-protocol/
                # e.g. server.cpu.temperature,cpu.id=0 42
                if 'model' in sensor_dict and 'channel' in sensor_dict and 'id' in sensor_dict and 'time' in sensor_dict:
                    utc_millis_ts = int(datetime.now(timezone.utc).timestamp() * 1000)

                    if 'temperature_C' in sensor_dict and 'humidity' in sensor_dict:
                        temperature = sensor_dict["temperature_C"]
                        humidity = sensor_dict["humidity"]
                        metric_line1 = f"thermometer.temperature,model={sensor_dict['model']},channel={sensor_dict['channel']},id={sensor_dict['id']} {temperature} {utc_millis_ts}"
                        metric_line2 = f"thermometer.humidity,model={sensor_dict['model']},channel={sensor_dict['channel']},id={sensor_dict['id']} {humidity} {utc_millis_ts}"
                        _put_metric(metric_line1)
                        _put_metric(metric_line2)

                    elif 'state' in sensor_dict and 'unit' in sensor_dict and 'group' in sensor_dict:
                        state_value = 1 if sensor_dict["state"].upper() == "ON" else 0
                        metric_line = f"infraredsensor.detectionstatus,model={sensor_dict['model']},channel={sensor_dict['channel']},id={sensor_dict['id']},group={sensor_dict['group']},unit={sensor_dict['unit']} {state_value} {utc_millis_ts}"
                        _put_metric(metric_line)

                    else:
                        log.warning(f"Unknown sensor type: {line}")
                else:
                    log.warning(f"Wrong sensor data fields: {line}")
            except JSONDecodeError:
                log.warning(f"Invalid JSON: {line}")
    except KeyboardInterrupt:
        log.info("Pipeline process interrupted!")
    finally:
        proc.terminate()
        cease_continuous_run.set()

    log.info("Pipeline process finished")
