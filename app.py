import asyncio
import contextlib
from fractions import Fraction
import os
from pathlib import Path
import queue
import re
import threading
import time
import uuid

from flask import Flask, jsonify, render_template, request
import serial
from serial.tools import list_ports

try:
    import av
except Exception:
    av = None

try:
    import numpy as np
except Exception:
    np = None

try:
    import sounddevice as sd
except Exception:
    sd = None

try:
    from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
except Exception:
    MediaStreamTrack = None
    RTCPeerConnection = None
    RTCSessionDescription = None

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
    "C4FM": "0E",
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
    "E": "C4FM",
    "0E": "C4FM",
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


VOIP_STACK_READY = all((MediaStreamTrack, RTCPeerConnection, RTCSessionDescription, av, np, sd))


class NullAudioSink:
    def start(self):
        return None

    def stop(self):
        return None

    def push_frame(self, frame, enabled=True):
        return None


if VOIP_STACK_READY:
    class SoundDeviceInputTrack(MediaStreamTrack):
        kind = "audio"

        def __init__(self, device=None, sample_rate=48000, channels=1, frame_samples=960):
            super().__init__()
            self._sample_rate = int(sample_rate)
            self._channels = int(channels)
            self._frame_samples = int(frame_samples)
            self._pts = 0
            self._frames = queue.Queue(maxsize=80)
            self._stream = sd.InputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="int16",
                device=device,
                blocksize=self._frame_samples,
                callback=self._on_audio,
            )
            self._stream.start()

        def _on_audio(self, indata, frames, timing, status):
            del frames, timing
            if status:
                return

            mono = indata[:, 0] if indata.ndim == 2 else indata
            if indata.ndim == 2 and indata.shape[1] > 1:
                mono = np.mean(indata, axis=1)
            mono = np.clip(mono, -32768, 32767).astype(np.int16, copy=False)

            try:
                self._frames.put_nowait(mono.copy())
            except queue.Full:
                with contextlib.suppress(queue.Empty):
                    self._frames.get_nowait()
                with contextlib.suppress(queue.Full):
                    self._frames.put_nowait(mono.copy())

        async def recv(self):
            try:
                samples = await asyncio.to_thread(self._frames.get, True, 1.0)
            except queue.Empty:
                samples = np.zeros(self._frame_samples, dtype=np.int16)

            if samples.size == 0:
                samples = np.zeros(self._frame_samples, dtype=np.int16)

            frame = av.AudioFrame(format="s16", layout="mono", samples=int(samples.shape[0]))
            frame.planes[0].update(samples.tobytes())
            frame.sample_rate = self._sample_rate
            frame.pts = self._pts
            frame.time_base = Fraction(1, self._sample_rate)
            self._pts += int(samples.shape[0])
            return frame

        def stop(self):
            if self._stream:
                with contextlib.suppress(Exception):
                    self._stream.stop()
                with contextlib.suppress(Exception):
                    self._stream.close()
                self._stream = None
            super().stop()


    class SoundDeviceAudioSink(NullAudioSink):
        def __init__(self, device=None, sample_rate=48000, channels=1):
            self._sample_rate = int(sample_rate)
            self._channels = int(channels)
            
            # Simple FIFO queue
            self._frames = queue.Queue(maxsize=30)
            
            # Straightforward PyAV Resampler
            self._resampler = av.AudioResampler(
                format="s16", 
                layout="mono", 
                rate=self._sample_rate
            )
            
            # Remainder buffer for chunk mismatch
            self._pending_audio = np.zeros((0, 1), dtype=np.int16)
            
            self._stream = sd.OutputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="int16",
                device=device,
                callback=self._on_audio,
            )

        def _to_mono_int16(self, frame):
            # PyAV to_ndarray() always returns (channels, samples).
            # Transposing it (.T) converts it to (samples, channels) which SoundDevice needs.
            samples = frame.to_ndarray().T
            
            # Since we requested layout='mono', channels=1, shape is now (samples, 1).
            if np.issubdtype(samples.dtype, np.floating):
                samples = np.clip(samples, -1.0, 1.0) * 32767.0
            
            return samples.astype(np.int16, copy=False)

        def _on_audio(self, outdata, frames, timing, status):
            needed = frames
            filled = 0

            # 1. Drain pending audio
            if self._pending_audio.shape[0] > 0:
                take = min(needed, self._pending_audio.shape[0])
                outdata[:take, 0] = self._pending_audio[:take, 0]
                self._pending_audio = self._pending_audio[take:]
                filled += take
                needed -= take

            # 2. Drain from queue
            while needed > 0:
                try:
                    chunk = self._frames.get_nowait()
                    take = min(needed, chunk.shape[0])
                    outdata[filled:filled+take, 0] = chunk[:take, 0]
                    
                    if chunk.shape[0] > take:
                        self._pending_audio = chunk[take:]
                    else:
                        self._pending_audio = np.zeros((0, 1), dtype=np.int16)
                    
                    filled += take
                    needed -= take
                except queue.Empty:
                    break

            # 3. Fill remainder with zeros
            if needed > 0:
                outdata[filled:, 0].fill(0)

            # 4. Copy to all output channels just in case
            if outdata.shape[1] > 1:
                for c in range(1, outdata.shape[1]):
                    outdata[:, c] = outdata[:, 0]

        def start(self):
            try:
                self._stream.start()
            except Exception as e:
                print(f"Error starting SoundDevice output: {e}")

        def stop(self):
            with contextlib.suppress(Exception):
                self._stream.stop()
                self._stream.close()

        def push_frame(self, frame, enabled=True):
            # If not transmitting, just discard the frame immediately
            if not enabled:
                return
                
            try:
                resampled = self._resampler.resample(frame)
                if not resampled:
                    return
                for f in resampled:
                    mono = self._to_mono_int16(f)
                    try:
                        self._frames.put_nowait(mono)
                    except queue.Full:
                        with contextlib.suppress(queue.Empty):
                            self._frames.get_nowait()
                        with contextlib.suppress(queue.Full):
                            self._frames.put_nowait(mono)
            except Exception as e:
                print(f"VoIP Transmission Error: {e}")



else:
    SoundDeviceInputTrack = None

    class SoundDeviceAudioSink(NullAudioSink):
        pass


class VoipRuntime:
    def __init__(self):
        self._lock = threading.RLock()
        self._pc = None
        self._rx_track = None
        self._tx_sink = NullAudioSink()
        self._inbound_task = None
        self._loop = None
        self._loop_thread = None
        self._tx_enabled = False
        self._session_id = None
        self._connection_state = "idle"
        self._last_error = ""
        self.audio_input_device = os.getenv("FT991_AUDIO_RX_DEVICE", "")
        self.audio_output_device = os.getenv("FT991_AUDIO_TX_DEVICE", "")

        if VOIP_STACK_READY:
            self._loop = asyncio.new_event_loop()
            self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
            self._loop_thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_async(self, coroutine, timeout=15):
        if not self._loop:
            raise RuntimeError("VoIP stack is unavailable.")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        return future.result(timeout=timeout)

    def _parse_device(self, raw_value):
        value = str(raw_value or "").strip()
        if not value:
            return None
        return int(value) if value.isdigit() else value

    def list_audio_devices(self):
        if not sd:
            return {"inputs": [], "outputs": []}

        inputs = []
        outputs = []
        for index, device in enumerate(sd.query_devices()):
            name = str(device.get("name", f"Device {index}"))
            if int(device.get("max_input_channels", 0)) > 0:
                inputs.append({"id": str(index), "label": name})
            if int(device.get("max_output_channels", 0)) > 0:
                outputs.append({"id": str(index), "label": name})

        return {"inputs": inputs, "outputs": outputs}

    async def _close_peer_async(self):
        if self._inbound_task:
            self._inbound_task.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await self._inbound_task
            self._inbound_task = None

        if self._pc:
            with contextlib.suppress(Exception):
                await self._pc.close()
            self._pc = None

        if self._rx_track:
            with contextlib.suppress(Exception):
                self._rx_track.stop()
            self._rx_track = None

        if self._tx_sink:
            with contextlib.suppress(Exception):
                self._tx_sink.stop()
            self._tx_sink = NullAudioSink()

    async def _consume_inbound_audio(self, track):
        try:
            while True:
                frame = await track.recv()
                with self._lock:
                    sink = self._tx_sink
                    enabled = self._tx_enabled
                sink.push_frame(frame, enabled=enabled)
        except Exception:
            return

    async def _connect_offer_async(self, offer_sdp, offer_type, input_device, output_device):
        await self._close_peer_async()

        input_choice = self._parse_device(input_device if input_device is not None else self.audio_input_device)
        output_choice = self._parse_device(output_device if output_device is not None else self.audio_output_device)

        tx_sink = SoundDeviceAudioSink(device=output_choice)
        tx_sink.start()
        rx_track = SoundDeviceInputTrack(device=input_choice)

        pc = RTCPeerConnection()
        pc.addTrack(rx_track)

        @pc.on("track")
        def on_track(track):
            if track.kind != "audio":
                return
            self._inbound_task = asyncio.create_task(self._consume_inbound_audio(track))

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            state = pc.connectionState
            with self._lock:
                self._connection_state = state
            if state in {"failed", "closed", "disconnected"}:
                with self._lock:
                    self._tx_enabled = False

        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type=offer_type))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        with self._lock:
            self._pc = pc
            self._rx_track = rx_track
            self._tx_sink = tx_sink
            self._tx_enabled = False
            self._session_id = uuid.uuid4().hex[:10]
            self._connection_state = "connecting"
            self._last_error = ""
            if input_device is not None:
                self.audio_input_device = str(input_device)
            if output_device is not None:
                self.audio_output_device = str(output_device)

        return {
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type,
        }

    def connect_offer(self, offer_sdp, offer_type, input_device=None, output_device=None):
        if not VOIP_STACK_READY:
            return False, "VoIP stack is unavailable. Install aiortc, av, numpy, and sounddevice.", None

        try:
            answer = self._run_async(
                self._connect_offer_async(offer_sdp, offer_type, input_device, output_device),
                timeout=20,
            )
            return True, "VoIP WebRTC session started.", answer
        except Exception as exc:
            err_type = type(exc).__name__
            err_msg = str(exc)
            print(f"VoIP connect error: {err_type}: {err_msg}")
            import traceback
            traceback.print_exc()
            with self._lock:
                self._last_error = f"{err_type}: {err_msg}"
                self._connection_state = "error"
                self._tx_enabled = False
            return False, f"VoIP connect failed: {err_type}: {err_msg}", None

    def disconnect(self):
        if not self._loop:
            with self._lock:
                self._tx_enabled = False
                self._session_id = None
                self._connection_state = "idle"
            return True, "VoIP is already inactive."

        try:
            self._run_async(self._close_peer_async(), timeout=10)
            with self._lock:
                self._tx_enabled = False
                self._session_id = None
                self._connection_state = "idle"
            return True, "VoIP session stopped."
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
            return False, f"Failed to stop VoIP session: {exc}"

    def set_tx_enabled(self, enabled):
        with self._lock:
            self._tx_enabled = bool(enabled)

    def set_audio_devices(self, input_device=None, output_device=None):
        with self._lock:
            if input_device is not None:
                self.audio_input_device = str(input_device)
            if output_device is not None:
                self.audio_output_device = str(output_device)

    def get_audio_devices(self):
        with self._lock:
            return {
                "audio_input_device": self.audio_input_device,
                "audio_output_device": self.audio_output_device,
            }

    def status(self):
        with self._lock:
            return {
                "stack_ready": bool(VOIP_STACK_READY),
                "session_id": self._session_id,
                "connection_state": self._connection_state,
                "tx_enabled": self._tx_enabled,
                "audio_input_device": self.audio_input_device,
                "audio_output_device": self.audio_output_device,
                "last_error": self._last_error,
            }


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
voip = VoipRuntime()


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
    voip_state = voip.status()
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
            "tuner": "N/A",
            "voip": voip_state,
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
        "voip": voip_state,
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


def _env_truthy(name, default="0"):
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_path(path_value):
    raw = str(path_value or "").strip()
    if not raw:
        return None

    path = Path(raw)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    return path.resolve()


def _find_cert_pair_from_dir(cert_dir):
    if not cert_dir.exists() or not cert_dir.is_dir():
        return None, None

    name_hint = str(os.getenv("FT991_TLS_NAME", "")).strip()
    key_candidates = sorted(cert_dir.glob("*-key.pem"))
    if name_hint:
        key_candidates = [item for item in key_candidates if item.name.startswith(name_hint)]

    for key_path in key_candidates:
        cert_name = key_path.name.replace("-key.pem", ".pem")
        cert_path = cert_dir / cert_name
        if cert_path.exists():
            return cert_path.resolve(), key_path.resolve()

    return None, None


def _resolve_ssl_context():
    if not _env_truthy("FT991_HTTPS", "0"):
        return None, "HTTP mode"

    cert_path = _resolve_path(os.getenv("FT991_TLS_CERT", ""))
    key_path = _resolve_path(os.getenv("FT991_TLS_KEY", ""))
    if cert_path or key_path:
        if not (cert_path and key_path):
            raise RuntimeError("Both FT991_TLS_CERT and FT991_TLS_KEY must be provided together.")
        if not cert_path.exists():
            raise RuntimeError(f"TLS certificate file not found: {cert_path}")
        if not key_path.exists():
            raise RuntimeError(f"TLS key file not found: {key_path}")
        return (str(cert_path), str(key_path)), f"cert={cert_path.name}, key={key_path.name}"

    cert_dir = _resolve_path(os.getenv("FT991_TLS_DIR", "cert"))
    auto_cert, auto_key = _find_cert_pair_from_dir(cert_dir) if cert_dir else (None, None)
    if auto_cert and auto_key:
        return (str(auto_cert), str(auto_key)), f"cert={auto_cert.name}, key={auto_key.name} (auto)"

    # Werkzeug can generate a temporary self-signed cert for local/LAN testing.
    return "adhoc", "adhoc self-signed certificate"


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


@app.get("/api/voip/audio_devices")
def api_voip_audio_devices():
    devices = voip.list_audio_devices()
    return jsonify({"ok": True, "devices": devices, "status": voip.status()})


@app.get("/api/voip/status")
def api_voip_status():
    return jsonify({"ok": True, "status": voip.status()})


@app.get("/api/voip/config")
def api_voip_config_get():
    return jsonify({"ok": True, "config": voip.get_audio_devices(), "status": voip.status()})


@app.post("/api/voip/config")
def api_voip_config_set():
    payload = _request_data()
    input_device = payload.get("audio_input_device")
    output_device = payload.get("audio_output_device")

    if input_device is None and output_device is None:
        return jsonify({"ok": False, "message": "No VoIP config fields provided."}), 400

    voip.set_audio_devices(input_device=input_device, output_device=output_device)
    return jsonify({
        "ok": True,
        "message": "VoIP station audio ports updated.",
        "config": voip.get_audio_devices(),
        "status": voip.status(),
    })


@app.post("/api/voip/connect")
def api_voip_connect():
    payload = _request_data()
    sdp = str(payload.get("sdp", "")).strip()
    sdp_type = str(payload.get("type", "offer")).strip().lower() or "offer"
    input_device = payload.get("audio_input_device")
    output_device = payload.get("audio_output_device")

    if not sdp:
        return jsonify({"ok": False, "message": "Missing SDP offer in request body."}), 400
    if sdp_type != "offer":
        return jsonify({"ok": False, "message": "SDP type must be offer."}), 400

    ok, message, answer = voip.connect_offer(
        offer_sdp=sdp,
        offer_type=sdp_type,
        input_device=input_device,
        output_device=output_device,
    )
    status_code = 200 if ok else 500
    return jsonify({"ok": ok, "message": message, "answer": answer, "status": voip.status()}), status_code


@app.post("/api/voip/disconnect")
def api_voip_disconnect():
    ok, message = voip.disconnect()
    status_code = 200 if ok else 500
    return jsonify({"ok": ok, "message": message, "status": voip.status()}), status_code


@app.post("/api/voip/ptt")
def api_voip_ptt():
    payload = _request_data()
    state = str(payload.get("state", "RX")).upper()
    if state not in {"TX", "RX"}:
        return jsonify({"ok": False, "message": "PTT state must be TX or RX."}), 400

    if not cat.connected:
        return jsonify({"ok": False, "message": "Serial port is not connected."}), 400

    command = "TX1;" if state == "TX" else "TX0;"
    if not cat.send(command):
        return jsonify({"ok": False, "message": "Failed to switch CAT PTT state."}), 500

    voip.set_tx_enabled(state == "TX")
    return jsonify({
        "ok": True,
        "message": f"VoIP and CAT switched to {state}.",
        "status": voip.status(),
    })


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
    voip.set_tx_enabled(state == "TX")
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

    host = os.getenv("FT991_HOST", "0.0.0.0")
    port = int(os.getenv("FT991_PORT_HTTP", "5000"))
    debug = _env_truthy("FT991_DEBUG", "1")
    try:
        ssl_context, ssl_note = _resolve_ssl_context()
    except Exception as exc:
        print(f"HTTPS configuration error: {exc}")
        raise SystemExit(1)

    if ssl_context:
        print(f"HTTPS enabled ({ssl_note}). Open https://localhost:{port} or https://<LAN-IP>:{port}")
    else:
        print(f"HTTP mode. Open http://localhost:{port} (LAN mic/camera may require HTTPS).")

    app.run(host=host, port=port, debug=debug, ssl_context=ssl_context)
#Made by Zorko Enterprise Labs