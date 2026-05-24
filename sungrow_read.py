#!/usr/bin/env python3
"""
sungrow_read.py — Read Sungrow SH6.0RT Modbus registers, write JSON to stdout,
                  and ingest metrics into Dynatrace.

Register map source: mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant
Addressing:  0-based (pymodbus); YAML comment "reg N" = address N-1.
Word order:  32-bit registers use little-endian word order (low word at lower address).

Requires:  pip install pymodbus requests

Environment variables:
  DT_API_TOKEN        Required for Dynatrace ingest; skips ingest if unset.
  DT_METRIC_INGEST_URL  Optional; defaults to the project sprint URL.

Usage:
  python3 sungrow_read.py [--host HOST] [--port PORT] [--slave SLAVE]

Defaults: host=192.168.86.26, port=502, slave=1

Sign conventions:
  meter_active_power   positive = exporting to grid (Einspeisung)
                       negative = importing from grid (Netzbezug)
  battery_power        positive = discharging, negative = charging
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import requests

import pymodbus as _pymodbus
_PYMODBUS_MAJOR = int(_pymodbus.__version__.split(".")[0])

if _PYMODBUS_MAJOR >= 3:
    from pymodbus.client import ModbusTcpClient
    _SLAVE_KWARG = "slave"
else:
    from pymodbus.client.sync import ModbusTcpClient
    _SLAVE_KWARG = "unit"

from pymodbus.exceptions import ModbusException

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)

DEFAULT_HOST = "192.168.86.26"
DEFAULT_PORT = 502
DEFAULT_SLAVE = 1

DT_METRIC_INGEST_URL = os.environ.get(
    "DT_METRIC_INGEST_URL",
    "https://rhp60717.sprint.dynatracelabs.com/api/v2/metrics/ingest",
)
DT_API_TOKEN = os.environ.get("DT_API_TOKEN")

METRIC_PREFIX = "sungrow_"

# fmt: (fc, address, name, dtype, scale, unit)
#   fc=4   input register
#   dtype: int16 | uint16 | int32 | uint32
#          int32/uint32 span two consecutive registers (low word at address, high at address+1)
TARGET_REGISTERS = [
    (4, 5007,  "inverter_temperature", "int16",  0.1, "°C"),  # reg 5008
    (4, 5016,  "total_dc_power",       "uint32", 1,   "W"),   # reg 5017-5018
    (4, 5600,  "meter_active_power",   "int32",  1,   "W"),   # reg 5601-5602
    (4, 13007, "load_power",           "int32",  1,   "W"),   # reg 13008-13009
    (4, 13021, "battery_power",        "int16",  1,   "W"),   # reg 13022
    (4, 13022, "battery_level",        "uint16", 0.1, "%"),   # reg 13023
]

READ_BLOCKS = [
    (4, 4999, 64),   # input 4999-5062: inv temp (5007), total DC power (5016-5017)
    (4, 5600, 2),    # input 5600-5601: meter active power int32
    (4, 13000, 25),  # input 13000-13024: load power (13007-13008), battery (13021-13022)
]


def read_block(client, fc, start, count, slave):
    kwargs = {_SLAVE_KWARG: slave}
    try:
        resp = (
            client.read_input_registers(start, count, **kwargs)
            if fc == 4
            else client.read_holding_registers(start, count, **kwargs)
        )
        if resp.isError():
            log.warning("Modbus error fc=%d addr=%d count=%d: %s", fc, start, count, resp)
            return {}
        return {start + i: v for i, v in enumerate(resp.registers)}
    except ModbusException as exc:
        log.warning("Exception fc=%d addr=%d count=%d: %s", fc, start, count, exc)
        return {}


def decode(raw, fc, address, dtype):
    if dtype in ("int16", "uint16"):
        v = raw.get((fc, address))
        if v is None:
            return None
        if dtype == "int16" and v > 32767:
            v -= 65536
        return v
    low = raw.get((fc, address))
    high = raw.get((fc, address + 1))
    if low is None or high is None:
        return None
    v = (high << 16) | low
    if dtype == "int32" and v > 0x7FFFFFFF:
        v -= 0x100000000
    return v


def build_mint_lines(results, host, timestamp_ms):
    """Return Dynatrace MINT data lines, one per metric.

    Unit and displayName metadata are set once via the Settings API
    (builtin:metric.metadata) using dtctl — not inline here, as the
    MINT # metadata line is not supported on this environment.
    """
    lines = []
    for entry in results:
        if entry["value"] is None:
            continue
        metric_key = METRIC_PREFIX + entry["name"]
        lines.append(f"{metric_key},device={host} {entry['value']} {timestamp_ms}")
    return lines


def send_to_dynatrace(url, token, lines):
    body = "\n".join(lines) + "\n"
    headers = {
        "Authorization": f"Api-Token {token}",
        "Content-Type": "text/plain; charset=utf-8",
    }
    try:
        log.info("Sending %d lines to Dynatrace", len(lines))
        resp = requests.post(url, headers=headers, data=body.encode("utf-8"))
        resp.raise_for_status()
        log.info("Dynatrace response: %d %s", resp.status_code, resp.text.strip())
    except requests.exceptions.RequestException as exc:
        log.error("Failed to send to Dynatrace: %s", exc)


def main():
    parser = argparse.ArgumentParser(
        description="Read Sungrow SH6.0RT Modbus registers, write JSON, send to Dynatrace."
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--slave", type=int, default=DEFAULT_SLAVE)
    args = parser.parse_args()

    client = ModbusTcpClient(args.host, port=args.port)
    if not client.connect():
        log.error("Failed to connect to %s:%d", args.host, args.port)
        sys.exit(1)
    log.info("Connected to %s:%d slave=%d", args.host, args.port, args.slave)

    raw = {}
    for fc, start, count in READ_BLOCKS:
        for addr, val in read_block(client, fc, start, count, args.slave).items():
            raw[(fc, addr)] = val
    client.close()

    timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    results = []
    for fc, address, name, dtype, scale, unit in TARGET_REGISTERS:
        v = decode(raw, fc, address, dtype)
        value = None if v is None else (round(v * scale, 4) if scale != 1 else v)
        results.append({"name": name, "value": value, "unit": unit})

    # stdout — always
    output = {
        "timestamp": datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat(),
        "host": args.host,
        "registers": results,
    }
    json.dump(output, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")

    # Dynatrace ingest — only if token is set
    if DT_API_TOKEN:
        mint_lines = build_mint_lines(results, args.host, timestamp_ms)
        send_to_dynatrace(DT_METRIC_INGEST_URL, DT_API_TOKEN, mint_lines)
    else:
        log.warning("DT_API_TOKEN not set — skipping Dynatrace ingest")


if __name__ == "__main__":
    main()
