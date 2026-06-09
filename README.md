# PM3585 Tools

Utilities for converting Philips PM3580/PM3585 logic analyzer files to open formats,
and for capturing data directly from the instrument via RS-232.

## Scripts

### `pm3585_to_png.py` — Hardcopy screen dump to PNG

Converts PM3585 hardcopy (screen dump) files to PNG images, or captures the screen
directly from the instrument via RS-232.

The hardcopy format stores a 2-bit-per-pixel greyscale image with a 16-byte header containing the file magic (`61 0A 59 26`), version, image dimensions, and image data size. The converter validates all header fields before decoding.

```
python pm3585_to_png.py <input_file> [-o output.png]
python pm3585_to_png.py --port <port> [-o output.png]
```

If no output filename is given, `.png` is appended to the input filename, or `screen.png` is used when capturing live.

| Option | Description |
|---|---|
| `--port PORT` | Capture screen directly from the instrument via RS-232 |
| `--baud RATE` | Baud rate (default: 19200, `--port` only) |
| `--no-handshake` | Disable RTS/CTS handshaking (`--port` only) |
| `-v` | Verbose output (`--port` only) |

```sh
# Convert a saved hardcopy file
python pm3585_to_png.py screen.hcp

# Capture and convert directly from the instrument
python pm3585_to_png.py --port /dev/tty.usbserial-110 -o screenshot.png
```

---

### `pm3585_to_vcd.py` — Measurement file to VCD / CSV

Converts PM3585 logic analyzer measurement files (format versions 3 and 4) to [VCD](https://en.wikipedia.org/wiki/Value_change_dump) or CSV, suitable for viewing in [PulseView](https://sigrok.org/wiki/PulseView) / [sigrok](https://sigrok.org/), or captures and converts directly from the instrument via RS-232.

Supports timing mode, state mode, timing+glitch, and state+timing captures. Signal names are read from the label/clock descriptors stored in the file's settings section where available, falling back to generic `podN_chN` names.

```
python pm3585_to_vcd.py <input_file> [options]
python pm3585_to_vcd.py --port <port> [options]
```

| Option | Description |
|---|---|
| `--port PORT` | Capture measurement directly from the instrument via RS-232 |
| `-o FILE` | Output file (default: input filename with `.vcd`/`.csv`, or `capture.vcd`/`.csv` for `--port`) |
| `-f vcd\|csv` | Output format (default: `vcd`) |
| `--ref` | Convert the REF measurement instead of NEW |
| `--timing` | Force timing data for mode-3 (State+Timing) captures |
| `--buses` | Group multi-bit labels into VCD bus signals (experimental; may not display correctly in PulseView) |
| `--info` | Print file information and exit without converting |
| `--baud RATE` | Baud rate (default: 19200, `--port` only) |
| `--no-handshake` | Disable RTS/CTS handshaking (`--port` only) |
| `--trigger` | Send `:INITiate` to start a new acquisition, wait for completion, then capture (`--port` only) |
| `--stop` | Send `:STOP` (manual trigger) before capturing (`--port` only) |
| `--timeout SECS` | Acquisition wait timeout in seconds (default: 120, `--trigger` only) |
| `-v` | Verbose output |

```sh
# Convert a saved measurement file to VCD
python pm3585_to_vcd.py measurement.mea

# Convert to CSV
python pm3585_to_vcd.py measurement.mea -f csv

# Show file information
python pm3585_to_vcd.py measurement.mea --info --verbose

# Convert the REF capture to a named VCD file
python pm3585_to_vcd.py measurement.mea --ref -o ref_capture.vcd

# Capture and convert directly from the instrument
python pm3585_to_vcd.py --port /dev/tty.usbserial-110 -o out.vcd

# Trigger a new acquisition, then capture and convert
python pm3585_to_vcd.py --port /dev/tty.usbserial-110 --trigger -o out.vcd
```

---

### `pm3585_capture.py` — Save raw measurement or hardcopy file via RS-232

Pulls the current acquisition or screen image from a live PM3580/PM3585 over RS-232
and saves it as a raw file. Useful when you want to keep the native file for archiving
or later conversion. For direct capture-and-convert workflows, use `--port` on the
converter scripts instead.

Requires the RS-232 remote control option (PF8653/30) to be installed and enabled on the analyzer (I/O menu → Remote: RS-232). The port must be configured for **8 data bits** — binary transfer does not work with 7-bit mode.

#### Analyzer I/O menu settings

| Setting | Value |
|---|---|
| Remote | RS-232 |
| Baud | match `--baud` (default 19200) |
| Data | **8 bit** (required) |
| Parity | None |
| Stop | 1 |
| Handshaking | RTS/CTS (recommended) or None — see `--no-handshake` |

```
python pm3585_capture.py <port> <output_file> [options]
```

| Option | Description |
|---|---|
| `--baud RATE` | Baud rate: 75/150/300/1200/2400/4800/9600/19200 (default: 19200) |
| `--screen` | Capture screen hardcopy instead of measurement data |
| `--ref` | Capture the REF measurement instead of NEW (mutually exclusive with `--screen`) |
| `--trigger` | Send `:INITiate` to start a new acquisition, wait for it to complete, then capture |
| `--stop` | Send `:STOP` (manual trigger) before capturing |
| `--no-handshake` | Disable RTS/CTS hardware handshaking (use if your cable does not wire RTS/CTS) |
| `--timeout SECS` | Acquisition wait timeout in seconds (default: 120) |
| `--no-check` | Skip the file format compatibility check on received data |
| `-v` | Verbose output |

```sh
# Capture current NEW measurement (macOS/Linux), RTS/CTS on by default
python pm3585_capture.py /dev/tty.usbserial-110 capture.mea

# If your cable does not wire RTS/CTS
python pm3585_capture.py /dev/tty.usbserial-110 capture.mea --no-handshake

# Trigger a new acquisition, wait for it, then capture
python pm3585_capture.py /dev/tty.usbserial-110 capture.mea --trigger

# Capture screen hardcopy (then convert separately)
python pm3585_capture.py /dev/tty.usbserial-110 screen.hcp --screen
python pm3585_to_png.py screen.hcp

# Capture on Windows
python pm3585_capture.py COM3 capture.mea
```

---

### `pm3585_disk.py` — Diskette file manager

List, download, upload, delete, copy, and rename files on the instrument's
diskette via RS-232.

```
python pm3585_disk.py <port> <command> [args] [options]
```

| Command | Description |
|---|---|
| `ls` | List files on diskette (with disk usage summary) |
| `get REMOTE [LOCAL]` | Download a file; local path defaults to the remote filename |
| `put LOCAL [REMOTE]` | Upload a file; remote name defaults to the local filename (max 12 chars) |
| `delete REMOTE` | Delete a file from diskette (aliases: `del`, `rm`) |
| `copy SRC DST` | Copy a file on diskette (alias: `cp`) |
| `move SRC DST` | Rename/move a file on diskette (aliases: `mv`, `rename`) |

| Option | Description |
| --- | --- |
| `--baud RATE` | Baud rate (default: 19200) |
| `--no-handshake` | Disable RTS/CTS handshaking |
| `-v` | Verbose output |

```sh
# List files
python pm3585_disk.py /dev/tty.usbserial-110 ls

# Download a measurement file
python pm3585_disk.py /dev/tty.usbserial-110 get MEAS.001

# Download with a specific local name
python pm3585_disk.py /dev/tty.usbserial-110 get MEAS.001 capture.mea

# Upload a file to diskette
python pm3585_disk.py /dev/tty.usbserial-110 put capture.mea MEAS.002

# Delete a file
python pm3585_disk.py /dev/tty.usbserial-110 delete MEAS.002

# Copy / rename
python pm3585_disk.py /dev/tty.usbserial-110 copy MEAS.001 MEAS.BAK
python pm3585_disk.py /dev/tty.usbserial-110 move MEAS.BAK MEAS.OLD
```

---

### `pm3585_serial.py` — RS-232 / SCPI interface module

Shared library used by the other scripts. Handles serial port setup, SCPI command
and query exchange, IEEE-488.2 definite-length block reassembly, and ESC-byte
decoding. Import it directly if you want to integrate PM3585 communication into
your own scripts:

```python
import pm3585_serial as la

port = la.open_port("/dev/tty.usbserial-110", baud=19200)
print(la.identify(port))
data = la.dump_measurement(port)   # bytes: native PM3585 measurement file
screen = la.capture_screen(port)   # bytes: native PM3585 hardcopy file
port.close()
```

## Requirements

- Python 3.8+
- [Pillow](https://python-pillow.org/) (`pip install pillow`) — required by `pm3585_to_png.py` only
- [pyserial](https://pyserial.readthedocs.io/) (`pip install pyserial`) — required for any RS-232 functionality

## License

This project is licensed under the GNU General Public License v2.0 only.
See `LICENSE.md` for the full license text.

## File format references

- Hardcopy format: Philips PM3585 User Guide
- Measurement format: `MEAS_3_4.MAN` (distributed on PM3585 System Disk 2.0, October 7 1992)
