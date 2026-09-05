# FT-991A Web CAT Controller

Browser-based control panel for the Yaesu FT-991A over CAT serial control.

This project uses Flask + pyserial to control frequency, mode, RF power, squelch,
PTT, radio power, and tuner functions from a local web UI.

> [!WARNING]
> **This is a development branch (`Development-101`).**
>
> This branch contains work-in-progress code and may be unstable, incomplete, or contain experimental changes.
> 
> **Do not use this branch for production deployments.**

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

- [x] Live View (webcam panel)
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
- setup_station.py - cross-platform first-run setup and Linux service installer
- station.env.example - generic configuration reference
- station.windows.env.example - Windows configuration reference
- station.linux.env.example - Linux configuration reference
- cert/ - optional TLS certificate directory

## Requirements

- Python 3.10 or newer (tested with Python 3.11.6)
- A CAT-capable FT-991A connection via USB
- Matching CAT settings on the radio
- A current browser with WebRTC/media support (Chrome, Edge, or Firefox)
- Network access to the station only when LAN control is required

The setup script installs Python packages into a project-local virtual
environment. It does not modify the system Python installation.

Media API note:

- Microphone/camera APIs often require secure context.
- `http://localhost:5000` is usually allowed.
- `http://<LAN-IP>:5000` may block mic/camera in many browsers unless HTTPS is used.
- Embedded browsers (for example some in-app/Electron views) may not expose full media APIs.
- For LAN microphone/camera use, run Flask with HTTPS enabled (`FT991_HTTPS=1`).

## Choose Your Platform

- Follow [Windows Setup](#windows-setup) if the radio is connected to a Windows PC.
- Follow [Linux Setup](#linux-setup) if the radio is connected to a Linux PC.
- Do not mix the serial-device, virtual-environment, or service commands between
	the two platforms.

## Installation

### Windows Setup

> [!WARNING]
> Windows does not install a background service automatically. The setup
> command can start the application now, but Task Scheduler must be configured
> separately if it should start after reboot.

1. Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/windows/).
	Enable the option to add Python to `PATH`.
2. Connect the FT-991A by USB and install the Yaesu/USB serial driver if
	Windows does not show a COM port.
3. Open a new PowerShell window in the project directory. A new window avoids
	stale `FT991_*` variables from earlier experiments.
4. Run the guided setup:

```powershell
python setup_station.py --run
```

The script creates `.venv\Scripts\python.exe`, installs the packages, asks for
the website password, asks for the COM port (for example `COM7`), writes
`station.env`, generates local HTTPS certificate files when OpenSSL is
available, and starts Flask. Windows does not use systemd; the application
must be started manually after a reboot:

```powershell
.venv\Scripts\python.exe app.py
```

To make Windows start it automatically, create a Task Scheduler task that
runs `.venv\Scripts\python.exe app.py` with the project directory as its
working directory. Run the task as the same Windows user that owns the project
and store `station.env` in the project directory.

To identify the CAT port, open **Device Manager**, expand **Ports (COM & LPT)**,
and select the FT-991A entry labeled **Enhanced COM Port (COMx)**. Use the COM
number shown in parentheses, not the **Standard COM Port**, for CAT control.
Leave the port blank during setup if you want to select it in the web interface.

### Linux Setup

> [!WARNING]
> Linux serial access normally requires membership in `dialout` (or the
> distribution's equivalent group). Log out and back in after changing group
> membership. Do not run the application as root to bypass this requirement.

On Debian or Ubuntu, install the system prerequisites first:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip libportaudio2
```

Use the equivalent packages for Fedora, Arch, or another distribution. The
`libportaudio2` package is needed by the optional sounddevice/VoIP path.

Add your user to the serial-device group, then log out and back in (or start a
new login session):

```bash
sudo usermod -aG dialout "$USER"
```

Connect the radio and check its device name:

```bash
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```

Run setup with Python 3:

```bash
python3 setup_station.py --run
```

The script creates `.venv/bin/python`, installs the packages, asks for the
website password, asks for a device such as `/dev/ttyUSB0`, writes `station.env`,
 generates local HTTPS certificate files when OpenSSL is available, and installs
 the user service:
`~/.config/systemd/user/ft991a-remote.service`.

The service runs as the setup user, starts at boot through systemd user
lingering, and restarts after failures. Manage it with:

```bash
systemctl --user status ft991a-remote.service
systemctl --user restart ft991a-remote.service
journalctl --user -u ft991a-remote.service -f
systemctl --user disable --now ft991a-remote.service
```

If `systemctl` or user lingering is unavailable, setup keeps the service file
and prints a direct start command. The application is never installed or run
as root by this script.

### Manual setup

The guided script is recommended, but the equivalent manual flow is:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

On Windows, replace `.venv/bin/python` with `.venv\Scripts\python.exe`.

The setup script supports these maintenance options:

- `--force` regenerates the password hash and session secret.
- `--force-install` reinstalls the Python dependencies.
- `--venv PATH` uses a different virtual-environment directory.
- `--run` starts the application immediately, or starts the Linux service.

## Platform Run Instructions

### Windows Run

```powershell
.venv\Scripts\python.exe app.py
```

Open the HTTPS URL printed by the application, normally:
`https://127.0.0.1:5000`. A browser certificate warning is expected when using
the local development certificate. For LAN access, use the Windows computer's
LAN address and allow the selected port through Windows Firewall only for the
trusted operator network.

### Linux Run

For the recommended boot-managed service:

```bash
systemctl --user start ft991a-remote.service
systemctl --user status ft991a-remote.service
```

For a foreground diagnostic run:

```bash
.venv/bin/python app.py
```

Open the HTTPS URL printed by the application, normally:
`https://127.0.0.1:5000`. Use `journalctl --user -u ft991a-remote.service -f`
to view service output.

## Secure Login

The application requires a password login before serving the station console or
any API endpoint. There are no default credentials. Generate a random session
secret and a password hash:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
python -c "from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass(), method='scrypt'))"
```

Set the generated values in the current PowerShell session, then use HTTPS:

```powershell
$env:FT991_SECRET_KEY = "paste-the-random-secret-here"
$env:FT991_PASSWORD_HASH = "paste-the-generated-password-hash-here"
$env:FT991_HTTPS = "1"
$env:FT991_DEBUG = "0"
python app.py
```

On Linux, the setup script writes these values to `station.env` automatically.
For manual configuration, use the same variable names in the shell:

```bash
export FT991_SECRET_KEY="paste-the-random-secret-here"
export FT991_PASSWORD_HASH="paste-the-generated-password-hash-here"
export FT991_HTTPS=1
export FT991_DEBUG=0
.venv/bin/python app.py
```

The login uses an HTTP-only, Secure, SameSite cookie, CSRF protection for all
state-changing requests, and throttles failed attempts. Authenticated HTTP is
blocked by default; only set `FT991_ALLOW_INSECURE_HTTP=1` for a trusted local
test on a network you control.

The generated configuration can be reviewed against [station.env.example](station.env.example).
Do not commit `station.env`; it contains the session secret and password hash.
Explicit `FT991_*` environment variables take precedence over values in
`station.env`; use a new terminal or clear old variables after testing.

## Configuration

The application reads `station.env` next to `app.py` at startup. Values already
present in the process environment take precedence. This makes it possible to
keep stable settings in the file while overriding one value for a test run.

Important settings:

| Setting | Windows example | Linux example | Purpose |
| --- | --- | --- | --- |
| `FT991_PORT` | `COM7` | `/dev/ttyUSB0` | Radio CAT serial device |
| `FT991_BAUD` | `38400` | `38400` | CAT baud rate |
| `FT991_HOST` | `0.0.0.0` | `0.0.0.0` | Web bind address |
| `FT991_PORT_HTTP` | `5000` | `5000` | Web port |
| `FT991_HTTPS` | `1` | `1` | Enable HTTPS |
| `FT991_RTSCTS` | `0` | `0` | Enable hardware flow control |
| `FT991_TIMEOUT` | `1.0` | `1.0` | CAT read timeout in seconds |
| `FT991_AUTO_CONNECT` | `0` | `0` | Connect to the configured radio at startup |
| `FT991_DEBUG` | `0` | `0` | Flask debug mode; keep disabled for operation |

### Windows Configuration

Use a Windows serial name such as `COM7` for `FT991_PORT`. Find it in Device
Manager. The virtual-environment interpreter is
`.venv\Scripts\python.exe`, and Windows has no systemd service in this project.
Use Task Scheduler for automatic startup and Windows Defender Firewall to limit
LAN access.

Example Windows overrides for a temporary PowerShell session:

```powershell
$env:FT991_PORT = "COM7"
$env:FT991_BAUD = "38400"
$env:FT991_HOST = "127.0.0.1"
$env:FT991_HTTPS = "1"
.venv\Scripts\python.exe app.py
```

### Linux Configuration

Use a device path such as `/dev/ttyUSB0` or `/dev/ttyACM0` for `FT991_PORT`.
Confirm the setup user can open that device through `dialout`. The
virtual-environment interpreter is `.venv/bin/python`; systemd loads
`station.env` through the generated user unit.

Example Linux overrides for a temporary shell session:

```bash
export FT991_PORT=/dev/ttyUSB0
export FT991_BAUD=38400
export FT991_HOST=127.0.0.1
export FT991_HTTPS=1
.venv/bin/python app.py
```

For a local-only installation, change `FT991_HOST` to `127.0.0.1`. For LAN
access, keep `0.0.0.0`, use HTTPS, and restrict access with the host firewall
to the operator network. For public-internet access, using a private VPN such
as Tailscale or WireGuard is highly recommended and is safer than publishing
the application directly. Do not expose the station directly to the public
internet unless you have also completed the production deployment hardening
described below.

### Public internet warning

This application controls a physical radio and can transmit. The recommended
remote-access design is:

```text
Operator device -> VPN (Tailscale or WireGuard) -> private station network -> FT-991A console
```

Do not forward the Flask port directly from the internet. If public HTTPS is
unavoidable, use a valid certificate and reverse proxy, a production WSGI
server, firewall allow-lists, strong authentication, two-factor authentication,
rate limiting, security headers, monitoring, and a network-isolated station
computer. Treat the VPN as the default deployment choice, not an optional
convenience.

The generated config uses `FT991_HTTPS=1`, `FT991_DEBUG=0`, and refuses
authenticated plaintext HTTP unless `FT991_ALLOW_INSECURE_HTTP=1` is explicitly
set. The latter is suitable only for a trusted local test.

### Audio and VoIP settings

The VoIP bridge is optional. These settings select host audio devices by name
or numeric device ID:

- `FT991_AUDIO_RX_DEVICE` selects the station receive-audio input.
- `FT991_AUDIO_TX_DEVICE` selects the station transmit-audio output.

The browser still requires microphone permissions and a secure browser context.
On non-localhost addresses, use HTTPS. If audio is not needed, the CAT console
works without a functioning VoIP stack.

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
- FT991_DEBUG: Flask debug mode, 1 or 0 (default 0)
- FT991_SECRET_KEY: required random session-signing secret, at least 32 characters
- FT991_PASSWORD_HASH: required Werkzeug password hash for the web login
- FT991_ALLOW_INSECURE_HTTP: explicitly allow authenticated HTTP, 1 or 0 (default 0)
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
- VPN access is highly recommended for any remote use across the public internet.
- This release is not a substitute for a hardened public-facing deployment.

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

## Architecture

The project is intentionally small:

- `app.py` owns Flask routes, CAT serialization, status polling, VoIP runtime,
	authentication, and configuration loading.
- `templates/index.html` contains the station console and browser-side API
	calls.
- `templates/login.html` contains the authentication page.
- `static/style.css` contains the shared interface styling.
- `setup_station.py` is a standard-library-only bootstrapper. It creates the
	virtual environment, installs `requirements.txt`, generates credentials, and
	installs the Linux user service.
- `station.env` is local deployment state and must remain private.

CAT commands are serialized by the controller lock. A CAT timeout is treated as
a missed response, while an actual pyserial/OS I/O exception closes the port so
the operator can reconnect. Status polling is deliberately separate from the
browser controls, so a stale status response must not be used as confirmation
that a command succeeded.

## Development

Create an isolated environment and install the same dependencies used by the
bootstrapper:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Run the lightweight checks before submitting changes:

```bash
.venv/bin/python -m py_compile app.py setup_station.py
.venv/bin/python setup_station.py --help
```

On Windows, use `.venv\Scripts\python.exe` instead of `.venv/bin/python`.
Hardware-dependent CAT and audio behavior must be tested with the appropriate
radio and devices connected. Do not transmit unattended during development.

When contributing, keep secrets out of commits, preserve the platform branches
in `setup_station.py`, and update the relevant Windows/Linux documentation when
adding a setting or dependency. `station.env` and virtual environments are
ignored by `.gitignore`; configuration examples contain placeholders only.

## Troubleshooting

### The setup script is slow

The first run creates a virtual environment and downloads packages such as
PyAV, aiortc, NumPy, and sounddevice. Later runs compare the hash of
`requirements.txt` and skip installation when it has not changed. Use
`--force-install` only when you intentionally want to reinstall packages.

### The login page says authentication is not configured

Confirm that `station.env` exists beside `app.py` and contains both
`FT991_SECRET_KEY` and `FT991_PASSWORD_HASH`. The secret must be at least 32
characters. Start a new terminal if old `FT991_*` environment variables are
overriding the file.

### The radio does not appear

Windows:

1. Check **Device Manager -> Ports (COM & LPT)**.
2. Select the FT-991A **Enhanced COM Port**, not the Standard COM Port.
3. Confirm no other radio program has the COM port open.
4. Check the CAT baud rate and USB driver.

Linux:

1. Check `/dev/ttyUSB*` and `/dev/ttyACM*`.
2. Confirm the user belongs to `dialout` and has started a new login session.
3. Check `journalctl --user -u ft991a-remote.service` if running under systemd.

For both systems:

1. Confirm the radio CAT rate matches `FT991_BAUD`.
2. Try toggling `FT991_RTSCTS` only if the radio and cable support it.
3. Select the port in the Serial Link panel and reconnect.

### The COM connection drops after transmitting

The application fix is implemented: a normal CAT read timeout no longer closes
the open COM connection. A real pyserial or operating-system I/O error still
closes it so the port can be reconnected safely. If the physical device still
disappears after a 20 m transmission, reduce RF power and
check the USB cable, ferrites, cable routing, station grounding, and USB power.
Use a short, shielded cable and keep it away from the antenna feed line. Check
the application log and the operating system's USB/device logs to distinguish a
CAT timeout from a real USB reset.

On Windows, check **Device Manager** for the COM port disappearing and review
**Event Viewer -> Windows Logs -> System** for USB or driver events. Disable USB
selective suspend for testing and try a different USB port or cable.

On Linux, inspect the kernel and service logs:

```bash
dmesg --follow
journalctl --user -u ft991a-remote.service -f
```

Look for USB resets, serial-driver errors, or permission changes.

### Band preset changes mode but not frequency

1. Check CAT menu settings and timeout behavior.
2. Use raw CAT terminal to test FA/FB responses.
3. Confirm VFO behavior on the radio front panel.

### HTTPS or browser media does not work

1. Open the exact `https://` URL printed by the application.
2. Accept the local self-signed certificate warning during development.
3. Confirm the browser has microphone and camera permission.
4. Check that the selected host audio devices exist on the station computer.
5. For LAN access, check the host firewall and use the correct station IP.

### Linux service does not start

Run:

```bash
systemctl --user status ft991a-remote.service
journalctl --user -u ft991a-remote.service -n 100 --no-pager
```

Confirm the paths in the unit file still exist, `station.env` is readable by
the setup user, and the user service manager is available. Re-run setup after
changing the project location or virtual environment path.

## License

Made under Apache 2.0 License.
