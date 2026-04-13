# FT-991A Web CAT Controller

Browser-based control panel for the Yaesu FT-991A over CAT serial control.

This project uses Flask + pyserial to control frequency, mode, RF power, squelch,
PTT, radio power, and tuner functions from a local web UI.

![preview](https://raw.githubusercontent.com/Zorko-Enterprise-Labs/Yaesu-FT-991A-Remote/refs/heads/main/design/image.png)

## Features

- Live status polling (frequency, mode, RF power, squelch, power, PTT, tuner)
- Serial port picker with baud and RTS/CTS options
- Band presets with mode pairing
- Frequency set with CAT format compatibility and readback verification
- Mode switching with CAT fallback formats
- RF power and squelch controls (sliders + numeric input)
- PTT TX/RX switching
- WebRTC VoIP audio bridge (single operator, LAN)
- Host audio device routing for FT-991A USB codec RX/TX
- Radio power ON/OFF (with power-on burst retry)
- Tuner control (ON/OFF/Tune Start)
- Live webcam view panel
- Auto-reconnect behavior after browser refresh

## Future Editions Roadmap

- [ ] Live View (webcam panel)
- [ ] More control options (expanded radio menus and advanced CAT controls)
- [x] Live microphone stream (browser audio capture controls)
- [x] Live speaker/monitor audio stream in browser
- [ ] Save and load user profiles (bands, power presets, UI preferences)
- [ ] Multi-radio support in one dashboard

## Project Structure

- app.py - Flask server and CAT serial logic
- templates/index.html - web UI and client logic
- static/style.css - UI styling
- requirements.txt - Python dependencies

## Requirements

- Python 3.10+ (tested with Python 3.11.6)
- A CAT-capable FT-991A connection via USB
- Correct CAT settings on the radio
- A full browser with WebRTC/media support (Chrome/Edge/Firefox)

Media API note:

- Microphone/camera APIs often require secure context.
- `http://localhost:5000` is usually allowed.
- `http://<LAN-IP>:5000` may block mic/camera in many browsers unless HTTPS is used.
- Embedded browsers (for example some in-app/Electron views) may not expose full media APIs.
- For LAN microphone/camera use, run Flask with HTTPS enabled (`FT991_HTTPS=1`).

## Install

1. Clone the repository.
2. Install dependencies.

Windows PowerShell example:

```powershell
pip install -r requirements.txt
```

## Run

```powershell
python app.py
```

For LAN VoIP/mic/camera support, prefer HTTPS mode:

```powershell
$env:FT991_HTTPS = "1"
python app.py
```

Open:

http://127.0.0.1:5000

or

http://{your_computer_ip}:5000

If HTTPS mode is enabled, open:

https://127.0.0.1:5000

or

https://{your_computer_ip}:5000

Then in the UI:

1. Select COM port
2. Select baud rate
3. Connect

## Radio CAT Setup Notes

Match your FT-991A CAT settings to your chosen serial setup.

Typical settings used by this app:

- 8 data bits
- no parity
- 2 stop bits
- CAT rate usually 38400 (configurable)

If commands are unreliable, verify:

- correct COM port
- matching CAT baud rate
- CAT timeout / menu settings on radio
- RTS/CTS usage

## Environment Variables

Optional variables:

- FT991_PORT: default serial port at startup (example COM7)
- FT991_BAUD: default baud (default 38400)
- FT991_TIMEOUT: serial timeout in seconds (default 1.0)
- FT991_RTSCTS: hardware flow control, 1 or 0 (default 0)
- FT991_AUTO_CONNECT: auto-connect on app startup, 1 or 0 (default 0)
- FT991_HOST: Flask bind host (default 0.0.0.0)
- FT991_PORT_HTTP: Flask listen port (default 5000)
- FT991_DEBUG: Flask debug mode, 1 or 0 (default 1)
- FT991_HTTPS: enable HTTPS, 1 or 0 (default 0)
- FT991_TLS_CERT: optional TLS certificate path (PEM)
- FT991_TLS_KEY: optional TLS private key path (PEM)
- FT991_TLS_DIR: directory for automatic cert discovery (default cert)
- FT991_TLS_NAME: optional filename prefix hint for cert discovery

HTTPS certificate loading order:

1. If `FT991_TLS_CERT` + `FT991_TLS_KEY` are set, those files are used.
2. Otherwise app searches `FT991_TLS_DIR` for pairs like `name.pem` + `name-key.pem`.
3. If no pair is found, app falls back to Werkzeug `adhoc` self-signed cert.

VoIP audio routing variables:

- FT991_AUDIO_RX_DEVICE: station PC audio input device for radio receive audio (default empty = system default input)
- FT991_AUDIO_TX_DEVICE: station PC audio output device for radio transmit audio (default empty = system default output)

Power ON reliability tuning:

- FT991_POWER_ON_BURST_MS: how long PS1 is repeated (default 320)
- FT991_POWER_ON_STEP_MS: interval between PS1 sends (default 40)

Example:

```powershell
$env:FT991_PORT = "COM7"
$env:FT991_BAUD = "38400"
$env:FT991_POWER_ON_BURST_MS = "400"
python app.py
```

Warning: Serial port names vary by system (for example COM3, COM5, COM7). Select the port that matches your FT-991A in Device Manager.

## Supported CAT Control Endpoints

Main API routes:

- GET /api/ports
- POST /api/connect
- POST /api/disconnect
- GET /api/status
- POST /api/set/frequency
- POST /api/set/band
- POST /api/set/mode
- POST /api/set/rf_power
- POST /api/set/squelch
- POST /api/set/ptt
- POST /api/set/power
- POST /api/set/tuner
- POST /api/send_raw
- GET /api/voip/status
- GET /api/voip/audio_devices
- GET /api/voip/config
- POST /api/voip/connect
- POST /api/voip/disconnect
- POST /api/voip/ptt
- POST /api/voip/config

## VoIP Setup (LAN)

The VoIP path uses browser WebRTC media + Python aiortc signaling, and is currently intended for one operator on local network.

1. Set FT-991A USB audio codec as the preferred station audio path or choose device IDs in UI.
2. Start app and open the web console.
3. Refresh audio devices in "VoIP Audio Link" and select RX input / TX output.
4. Click "Start VoIP" and allow microphone permission in browser.
5. Use main "PTT TX" / "PTT RX" buttons to key CAT and VoIP audio gate together.

Safety:

- Keep manual supervision when transmitting.
- Always verify band/mode/power before pressing VoIP TX.
- This release is LAN-focused and does not include internet hardening/auth.

## Tuner Control

Tuner endpoint uses the FT-991A AC command family:

- AC001 - tuner ON
- AC000 - tuner OFF
- AC002 - tuner tune start

## Safety and Operating Notes

- Use at your own risk when transmitting.
- Always verify band, mode, and power before keying TX.
- Keep RF power low during tuner tests.
- Do not rely on remote control without proper station supervision.

## Troubleshooting

If connection fails:

1. Confirm no other software is holding the COM port.
2. Confirm radio CAT rate matches the app setting.
3. Try toggling RTS/CTS.
4. Disconnect/reconnect in UI.

If band preset changes mode but not frequency:

1. Check CAT menu settings and timeout behavior.
2. Use raw CAT terminal to test FA/FB responses.
3. Confirm VFO behavior on the radio front panel.

If nothing else works:

1. Make a issue ticked

## License

Made under Apache 2.0 License.
