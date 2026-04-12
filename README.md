# FT-991A Web CAT Controller

Browser-based control panel for the Yaesu FT-991A over CAT serial control.

This project uses Flask + pyserial to control frequency, mode, RF power, squelch,
PTT, radio power, and tuner functions from a local web UI.

## Features

- Live status polling (frequency, mode, RF power, squelch, power, PTT, tuner)
- Serial port picker with baud and RTS/CTS options
- Band presets with mode pairing
- Frequency set with CAT format compatibility and readback verification
- Mode switching with CAT fallback formats
- RF power and squelch controls (sliders + numeric input)
- PTT TX/RX switching
- Radio power ON/OFF (with power-on burst retry)
- Tuner control (ON/OFF/Tune Start)
- Live webcam view panel
- Auto-reconnect behavior after browser refresh

## Future Editions Roadmap

- [ ] Live View (webcam panel)
- [ ] More control options (expanded radio menus and advanced CAT controls)
- [ ] Live microphone stream (browser audio capture controls)
- [ ] Live speaker/monitor audio stream in browser
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

Open:

http://127.0.0.1:5000

or

http://{your_computer_ip}:5000

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

## License

Made under Apache 2.0 License.
