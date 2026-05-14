import os
import schedule
import time
import requests, sys
import subprocess
import json
from json import JSONDecodeError
from datetime import datetime
import queue
import threading

BUF_SIZE = 1000
q = queue.Queue(BUF_SIZE)

# REST settings
dtMetricIngestUrl = os.environ.get("DT_METRIC_INGEST_URL", "https://rhp60717.sprint.dynatracelabs.com/api/v2/metrics/ingest")
dtToken = os.environ.get("DT_API_TOKEN")

def schedulerJob():
    timestamp = datetime.now()
    print(f"SchedulerJob running at {timestamp}")
    lines = []
    while not q.empty():
        line = q.get()
        lines.append(line)
    send_metric_ingest(dtMetricIngestUrl, dtToken, lines)
    print(f"SchedulerJob finished at {timestamp} and has send {len(lines)} lines")

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
        @classmethod
        def run(cls):
            while not cease_continuous_run.is_set():
                schedule.run_pending()
                time.sleep(interval)

    continuous_thread = ScheduleThread()
    continuous_thread.daemon = True
    continuous_thread.start()
    return cease_continuous_run

def send_metric_ingest(url, token, lines):
    # see https://www.dynatrace.com/support/help/dynatrace-api/environment-api/metric-v2/post-ingest-metrics/#example
    apiToken = 'Api-Token ' + token
    print(f"Sending metric ingest lines: {lines}")

    body_metrics = '\n'.join(lines)+'\n'

    headers = {
        'Authorization': apiToken,
        'Content-Type': 'text/plain; charset=utf-8'
    }
    try:
        print(f'Sending lines for metric ingest: {body_metrics}')
        response_metrics = requests.post(url, headers=headers, data = body_metrics.encode('utf-8'))
        print(f'metrics response status: {response_metrics.status_code},  data: {response_metrics.text}')
    except requests.exceptions.RequestException as e:
        print(f"HTTP error sending metric ingest lines: {e}")
    return

def get_epochtime_ms():
    return int(round(time.time() * 1000))



if __name__ == '__main__':
    if not dtToken:
        print("ERROR: DT_API_TOKEN environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    print("Pipeline process started")
    proc = subprocess.Popen( ["/usr/local/bin/rtl_433", "-F","json"], stdout=subprocess.PIPE, universal_newlines=True )

    schedule.every(1).minutes.do(schedulerJob)
    cease_continuous_run  = run_continuously()

    try:
        while True:
            line = proc.stdout.readline()
            # if not q.full():
            #  q.put(line)

            try:
                sensor_dict = json.loads(line)
                # print(json.dumps(sensor_dict, indent=2))

                # see metric ingest protocol: https://www.dynatrace.com/support/help/how-to-use-dynatrace/metrics/metric-ingestion/metric-ingestion-protocol/
                # e.g. server.cpu.temperature,cpu.id=0 42
                if 'model' in sensor_dict and 'channel' in sensor_dict and 'id' in sensor_dict and 'time' in sensor_dict:
                    timestamp = sensor_dict["time"]
                    parsed_timestamp = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
                    utcMillisTS = int(parsed_timestamp.timestamp() * 1000)

                    if 'temperature_C' in sensor_dict and 'humidity' in sensor_dict:
                        sensor_name = f"{sensor_dict['model']}_{sensor_dict['channel']}_{sensor_dict['id']}"
                        temperature = sensor_dict["temperature_C"]
                        humidity = sensor_dict["humidity"]
                        # creating lines for schemaless metrics
                        metricLine1 = f"thermometer.temperature,model={sensor_dict['model']},channel={sensor_dict['channel']},id={sensor_dict['id']} {temperature} {utcMillisTS}"
                        metricLine2 = f"thermometer.humidity,model={sensor_dict['model']},channel={sensor_dict['channel']},id={sensor_dict['id']} {humidity} {utcMillisTS}"
                        if not q.full():
                            q.put(metricLine1)
                        if not q.full():
                            q.put(metricLine2)

                    elif 'state' in sensor_dict and 'unit' in sensor_dict  and 'group' in sensor_dict:
                        sensor_name = f"{sensor_dict['model']}_{sensor_dict['channel']}_{sensor_dict['id']}_{sensor_dict['group']}_{sensor_dict['unit']}"
                        state = sensor_dict["state"]
                        stateValue = 1 if state.upper() == "ON" else 0
                        # send_custom_metric_infrared(dtThermometerUrl, dtToken, sensor_name, stateValue, timestamp)
                        # creating lines for schemaless metrics
                        metricLine = f"infraredsensor.detectionstatus,model={sensor_dict['model']},channel={sensor_dict['channel']},id={sensor_dict['id']},group={sensor_dict['group']},unit={sensor_dict['unit']} {stateValue} {utcMillisTS}"
                        if not q.full():
                            q.put(metricLine)

                    else:
                        print(f"Unknown sensor type: {line}")
                else:
                    print(f"Wrong sensor data fields: {line}")
            except JSONDecodeError:
                print(f"Invalid JSON: {line}")
    except KeyboardInterrupt:
        print("Pipeline process interrupted!")

    cease_continuous_run.set()
print("Pipeline process finished")