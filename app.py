import os
import re
import threading
import time

from flask import Flask, jsonify, render_template, request
import serial
from serial.tools import list_ports

app = Flask(__name__)

MODE_TO_CODE = {
    "LSB": "01",
    "USB": "02",
    "CW": "03",
    "FM": "04",
    "AM": "05",
    "RTTY-L": "06",
    "CW-R": "07",
    "DATA-L": "08",
    "RTTY-U": "09",
    "DATA-U": "0A",
    "DATA-FM": "0B",
    "FM-N": "0C",
}

CODE_TO_MODE = {
    "1": "LSB",
    "01": "LSB",
    "2": "USB",
    "02": "USB",
    "3": "CW",
    "03": "CW",
    "4": "FM",
    "04": "FM",
    "5": "AM",
    "05": "AM",
    "6": "RTTY-L",
    "06": "RTTY-L",
    "7": "CW-R",
    "07": "CW-R",
    "8": "DATA-L",
    "08": "DATA-L",
    "9": "RTTY-U",
    "09": "RTTY-U",
    "A": "DATA-U",
    "0A": "DATA-U",
    "B": "DATA-FM",
    "0B": "DATA-FM",
    "C": "FM-N",
    "0C": "FM-N",
}

BAND_PRESETS = {
    "160m": (1850000, "LSB"),
    "80m": (3700000, "LSB"),
    "40m": (7100000, "LSB"),
    "30m": (10120000, "CW"),
    "20m": (14250000, "USB"),
    "17m": (18130000, "USB"),
    "15m": (21200000, "USB"),
    "12m": (24940000, "USB"),
    "10m": (28400000, "USB"),
    "6m": (50200000, "USB"),
    "2m": (145450000, "FM"),
    "70cm": (432200000, "USB"),
}

BAND_ALIASES = {
    "160": "160m",
    "80": "80m",
    "40": "40m",
    "30": "30m",
    "20": "20m",
    "17": "17m",
    "15": "15m",
    "12": "12m",
    "10": "10m",
    "6": "6m",
    "2": "2m",
    "70": "70cm",
}


def _request_data():
    if request.is_json:
        payload = request.get_json(silent=True)
        return payload or {}
    return request.form.to_dict()


def _clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


class FT991CatController:
    def __init__(self):
        self._lock = threading.RLock()
        self._serial = None
        self.port = os.getenv("FT991_PORT", "")
        self.baud = int(os.getenv("FT991_BAUD", "38400"))
        self.timeout = float(os.getenv("FT991_TIMEOUT", "1.0"))
        self.rtscts = os.getenv("FT991_RTSCTS", "0") == "1"

    @property
    def connected(self):
        return bool(self._serial and self._serial.is_open)

    def config(self):
        return {
            "port": self.port,
            "baud": self.baud,
            "timeout": self.timeout,
            "rtscts": self.rtscts,
            "connected": self.connected,
        }

    def connect(self, port=None, baud=None, rtscts=None):
        with self._lock:
            if port is not None:
                self.port = port.strip()
            if baud is not None:
                self.baud = int(baud)
            if rtscts is not None:
                self.rtscts = bool(rtscts)

            if not self.port:
                return False, "No serial port selected."

            if self.connected:
                self._serial.close()
                self._serial = None

            try:
                self._serial = serial.Serial(
                    self.port,
                    self.baud,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_TWO,
                    timeout=self.timeout,
                    rtscts=self.rtscts,
                )
                return True, f"Connected to {self.port} @ {self.baud} bps"
            except Exception as exc:
                self._serial = None
                return False, f"Serial open failed: {exc}"

    def disconnect(self):
        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.close()
            self._serial = None

    def _normalize_command(self, command):
        cat = (command or "").strip().upper()
        if not cat:
            return ""
        if not cat.endswith(";"):
            cat += ";"
        return cat

    def send(self, command):
        cat = self._normalize_command(command)
        if not cat:
            return False

        with self._lock:
            if not self.connected:
                return False
            try:
                self._serial.reset_input_buffer()
                self._serial.write(cat.encode("ascii"))
                self._serial.flush()
                return True
            except Exception:
                self.disconnect()
                return False

    def query(self, command):
        cat = self._normalize_command(command)
        if not cat:
            return None

        with self._lock:
            if not self.connected:
                return None
            try:
                self._serial.reset_input_buffer()
                self._serial.write(cat.encode("ascii"))
                self._serial.flush()
                reply = self._serial.read_until(b";")
                if not reply:
                    return None
                return reply.decode("ascii", errors="ignore").strip()
            except Exception:
                self.disconnect()
                return None


cat = FT991CatController()


def parse_frequency(response):
    if not response:
        return None
    match = re.search(r"FA(\d{9,11})", response)
    if not match:
        return None
    return int(match.group(1))


def parse_mode(response):
    if not response:
        return "N/A"
    match = re.search(r"MD(0?[0-9A-C])", response)
    if not match:
        return "N/A"
    code = match.group(1)
    return CODE_TO_MODE.get(code, CODE_TO_MODE.get(code[-1], "Unknown"))


def parse_level(response, command_prefix):
    if not response:
        return None
    match = re.search(rf"{command_prefix}0?(\d{{3}})", response)
    if match:
        return int(match.group(1))
    fallback = re.search(r"(\d{3})", response)
    if fallback:
        return int(fallback.group(1))
    return None


def parse_switch(response, command_prefix):
    if not response:
        return "N/A"
    match = re.search(rf"{command_prefix}([01])", response)
    if not match:
        return "N/A"
    return "ON" if match.group(1) == "1" else "OFF"


def parse_tuner_state(response):
    if not response:
        return "N/A"
    # AC command: AC000 (OFF), AC001 (ON), AC002 (TUNING START)
    match = re.search(r"AC0{0,2}([0-2])", response)
    if not match:
        return "N/A"
    code = match.group(1)
    return {
        "0": "OFF",
        "1": "ON",
        "2": "TUNING",
    }.get(code, "N/A")


def status_payload():
    if not cat.connected:
        return {
            "connected": False,
            "port": cat.port,
            "frequency_hz": None,
            "frequency": "N/A",
            "mode": "N/A",
            "rf_power": "N/A",
            "squelch": "N/A",
            "power": "N/A",
            "ptt": "N/A",
        }

    fa_reply = cat.query("FA;")
    md_reply = cat.query("MD0;") or cat.query("MD;")
    pc_reply = cat.query("PC;")
    sq_reply = cat.query("SQ0;") or cat.query("SQ;")
    ps_reply = cat.query("PS;")
    tx_reply = cat.query("TX;")
    ac_reply = cat.query("AC;")

    frequency_hz = parse_frequency(fa_reply)
    rf_power_val = parse_level(pc_reply, "PC")
    squelch_val = parse_level(sq_reply, "SQ")

    return {
        "connected": True,
        "port": cat.port,
        "frequency_hz": frequency_hz,
        "frequency": f"{(frequency_hz / 1_000_000):.6f} MHz" if frequency_hz else "N/A",
        "mode": parse_mode(md_reply),
        "rf_power": f"{rf_power_val} W" if rf_power_val is not None else "N/A",
        "squelch": str(squelch_val) if squelch_val is not None else "N/A",
        "power": parse_switch(ps_reply, "PS"),
        "ptt": "TX" if parse_switch(tx_reply, "TX") == "ON" else "RX",
        "tuner": parse_tuner_state(ac_reply),
        "raw": {
            "FA": fa_reply,
            "MD": md_reply,
            "PC": pc_reply,
            "SQ": sq_reply,
            "PS": ps_reply,
            "TX": tx_reply,
            "AC": ac_reply,
        },
    }


def send_or_error(command):
    if not cat.connected:
        return jsonify({"ok": False, "message": "Serial port is not connected."}), 400
    if not cat.send(command):
        return jsonify({"ok": False, "message": f"Failed to send CAT command {command}"}), 500
    return None


def send_power_on_burst():
    """Send PS1 repeatedly for a short period to handle occasional missed wake command."""
    if not cat.connected:
        return False, "Serial port is not connected."

    duration_ms = int(os.getenv("FT991_POWER_ON_BURST_MS", "320"))
    step_ms = int(os.getenv("FT991_POWER_ON_STEP_MS", "40"))
    step_ms = max(10, step_ms)
    duration_ms = max(step_ms, duration_ms)

    sent_ok = 0
    start = time.monotonic()
    while (time.monotonic() - start) * 1000 < duration_ms:
        if cat.send("PS1;"):
            sent_ok += 1
        time.sleep(step_ms / 1000.0)

    if sent_ok == 0:
        return False, "Failed to send PS1 burst."

    # Best-effort verification; some rigs may not answer immediately after wake.
    time.sleep(0.08)
    ps_reply = cat.query("PS;")
    power_state = parse_switch(ps_reply, "PS")
    if power_state == "ON":
        return True, f"Radio power ON confirmed after {sent_ok} PS1 commands."
    return True, f"Sent {sent_ok} PS1 commands over {duration_ms} ms (wake sent)."


def send_with_fallback(commands):
    """Try multiple CAT command formats until one send succeeds."""
    if not cat.connected:
        return False, "Serial port is not connected."

    last_cmd = None
    for cmd in commands:
        last_cmd = cmd
        if cat.send(cmd):
            return True, cmd
    return False, f"Failed to send CAT command {last_cmd}"


def normalize_band_name(raw_band):
    token = str(raw_band or "").strip().lower().replace(" ", "")
    token = token.replace("-", "")
    token = token.replace("meter", "m")
    token = token.replace("meters", "m")
    if token in BAND_PRESETS:
        return token
    return BAND_ALIASES.get(token, token)


def set_frequency_cat(freq_hz):
    if not cat.connected:
        return False, "Serial port is not connected."

    def read_vfo(prefix):
        reply = cat.query(f"{prefix};")
        if not reply:
            return None
        match = re.search(rf"{prefix}(\d{{9,11}})", reply)
        if not match:
            return None
        return int(match.group(1))

    # Apply to both VFO A and VFO B so the preset works even when operating on VFO-B.
    commands = [
        f"FA{freq_hz:09d};",
        f"FA{freq_hz:011d};",
        f"FB{freq_hz:09d};",
        f"FB{freq_hz:011d};",
    ]

    sent_count = 0
    for cmd in commands:
        if cat.send(cmd):
            sent_count += 1
            time.sleep(0.02)

    if sent_count == 0:
        return False, "Failed to send CAT frequency commands."

    # Verify by readback because serial write success does not guarantee CAT acceptance.
    for _ in range(3):
        fa_now = read_vfo("FA")
        fb_now = read_vfo("FB")
        if fa_now == freq_hz or fb_now == freq_hz:
            return True, f"Frequency confirmed (FA={fa_now}, FB={fb_now})."
        time.sleep(0.04)

    fa_now = read_vfo("FA")
    fb_now = read_vfo("FB")
    return False, f"Frequency not confirmed by readback (FA={fa_now}, FB={fb_now})."


def set_mode_cat(mode_name):
    mode_code = MODE_TO_CODE.get(mode_name)
    if not mode_code:
        return False, "Unsupported mode."

    # Try both 2-digit and 1-digit mode variants for CAT compatibility.
    compact = mode_code.lstrip("0") or "0"
    candidates = [f"MD{mode_code};", f"MD{compact};"]

    ok, detail = send_with_fallback(candidates)
    return ok, detail


@app.route("/")
def index():
    return render_template("index.html")


@app.get("/api/ports")
def api_ports():
    ports = [
        {
            "device": item.device,
            "description": item.description,
            "hwid": item.hwid,
        }
        for item in list_ports.comports()
    ]
    return jsonify({
        "ok": True,
        "ports": ports,
        "serial": cat.config(),
    })


@app.post("/api/connect")
def api_connect():
    payload = _request_data()
    port = payload.get("port")
    baud_raw = payload.get("baud", cat.baud)
    rtscts_raw = payload.get("rtscts", cat.rtscts)

    try:
        baud = int(baud_raw)
    except Exception:
        return jsonify({"ok": False, "message": "Invalid baud rate."}), 400

    rtscts = str(rtscts_raw).lower() in {"1", "true", "on", "yes"}
    ok, message = cat.connect(port=port, baud=baud, rtscts=rtscts)
    status = 200 if ok else 500
    return jsonify({"ok": ok, "message": message, "serial": cat.config()}), status


@app.post("/api/disconnect")
def api_disconnect():
    cat.disconnect()
    return jsonify({"ok": True, "message": "Serial connection closed.", "serial": cat.config()})


@app.get("/api/status")
def api_status():
    return jsonify({"ok": True, "status": status_payload()})


@app.post("/api/set/frequency")
def api_set_frequency():
    payload = _request_data()
    freq_raw = payload.get("frequency")
    try:
        freq_mhz = float(freq_raw)
    except Exception:
        return jsonify({"ok": False, "message": "Frequency must be numeric in MHz."}), 400

    freq_hz = int(freq_mhz * 1_000_000)
    if freq_hz <= 0:
        return jsonify({"ok": False, "message": "Frequency must be greater than 0."}), 400

    ok, detail = set_frequency_cat(freq_hz)
    if not ok:
        status = 400 if "not connected" in detail.lower() else 500
        return jsonify({"ok": False, "message": detail}), status
    return jsonify({"ok": True, "message": f"Frequency set to {freq_mhz:.6f} MHz."})


@app.post("/api/set/band")
def api_set_band():
    payload = _request_data()
    band = normalize_band_name(payload.get("band", ""))
    preset = BAND_PRESETS.get(band)
    if not preset:
        return jsonify({"ok": False, "message": "Unknown band preset."}), 400

    freq_hz, mode_name = preset

    ok, detail = set_frequency_cat(freq_hz)
    if not ok:
        status = 400 if "not connected" in detail.lower() else 500
        return jsonify({"ok": False, "message": detail}), status

    # Give the radio a brief settle window between preset frequency and mode changes.
    time.sleep(0.06)

    ok, detail = set_mode_cat(mode_name)
    if not ok:
        status = 400 if detail == "Unsupported mode." or "not connected" in detail.lower() else 500
        return jsonify({"ok": False, "message": detail}), status

    return jsonify({"ok": True, "message": f"Band {band} loaded ({mode_name}, {freq_hz / 1_000_000:.3f} MHz)."})


@app.post("/api/set/mode")
def api_set_mode():
    payload = _request_data()
    mode_name = str(payload.get("mode", "")).upper()
    if mode_name not in MODE_TO_CODE:
        return jsonify({"ok": False, "message": "Unsupported mode."}), 400

    ok, detail = set_mode_cat(mode_name)
    if not ok:
        status = 400 if "not connected" in detail.lower() else 500
        return jsonify({"ok": False, "message": detail}), status
    return jsonify({"ok": True, "message": f"Mode set to {mode_name}."})


@app.post("/api/set/rf_power")
def api_set_rf_power():
    payload = _request_data()
    value_raw = payload.get("rf_power")
    try:
        value = int(value_raw)
    except Exception:
        return jsonify({"ok": False, "message": "RF power must be an integer."}), 400

    value = _clamp(value, 5, 100)
    result = send_or_error(f"PC{value:03d};")
    if result:
        return result
    return jsonify({"ok": True, "message": f"RF power set to {value} W."})


@app.post("/api/set/squelch")
def api_set_squelch():
    payload = _request_data()
    value_raw = payload.get("squelch")
    try:
        value = int(value_raw)
    except Exception:
        return jsonify({"ok": False, "message": "Squelch must be an integer."}), 400

    value = _clamp(value, 0, 100)
    result = send_or_error(f"SQ0{value:03d};")
    if result:
        return result
    return jsonify({"ok": True, "message": f"Squelch set to {value}."})


@app.post("/api/set/ptt")
def api_set_ptt():
    payload = _request_data()
    state = str(payload.get("state", "RX")).upper()
    command = "TX1;" if state == "TX" else "TX0;"

    result = send_or_error(command)
    if result:
        return result
    return jsonify({"ok": True, "message": f"PTT switched to {state}."})


@app.post("/api/set/power")
def api_set_power():
    payload = _request_data()
    state = str(payload.get("state", "OFF")).upper()
    if state == "ON":
        ok, message = send_power_on_burst()
        status = 200 if ok else 500
        return jsonify({"ok": ok, "message": message}), status

    command = "PS0;"

    result = send_or_error(command)
    if result:
        return result
    return jsonify({"ok": True, "message": f"Radio power {state}."})


@app.post("/api/set/tuner")
def api_set_tuner():
    payload = _request_data()
    action = str(payload.get("action", "start")).strip().lower()

    command_map = {
        "off": "AC000;",
        "on": "AC001;",
        "start": "AC002;",
        "tune": "AC002;",
        "trigger": "AC002;",
    }
    command = command_map.get(action)
    if not command:
        return jsonify({"ok": False, "message": "Invalid tuner action. Use on, off, or start."}), 400

    result = send_or_error(command)
    if result:
        return result

    labels = {
        "AC000;": "Tuner OFF",
        "AC001;": "Tuner ON",
        "AC002;": "Tuner tune cycle started",
    }
    return jsonify({"ok": True, "message": labels.get(command, "Tuner command sent.")})


@app.post("/api/send_raw")
def api_send_raw():
    payload = _request_data()
    command = str(payload.get("command", "")).strip()
    expect_reply = str(payload.get("expect_reply", "1")).lower() in {"1", "true", "on", "yes"}

    if not command:
        return jsonify({"ok": False, "message": "CAT command is required."}), 400
    if not cat.connected:
        return jsonify({"ok": False, "message": "Serial port is not connected."}), 400

    if expect_reply:
        reply = cat.query(command)
        if reply is None:
            return jsonify({"ok": False, "message": "No CAT reply received.", "reply": None}), 500
        return jsonify({"ok": True, "message": "CAT query sent.", "reply": reply})

    if not cat.send(command):
        return jsonify({"ok": False, "message": "Failed to send CAT command."}), 500
    return jsonify({"ok": True, "message": "CAT command sent.", "reply": None})


if __name__ == "__main__":
    if os.getenv("FT991_AUTO_CONNECT", "0") == "1":
        ok, msg = cat.connect()
        print(msg)
    app.run(host="0.0.0.0", port=5000, debug=True)
#Made by Zorko Enterprise Labs