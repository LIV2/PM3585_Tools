# PM3585 Tools

Utilities for converting Philips PM3580/PM3585 logic analyzer files to open formats.

## Scripts

### `pm3585_to_png.py` — Hardcopy screen dump to PNG

Converts PM3585 hardcopy (screen dump) files to PNG images.

The hardcopy format stores a 2-bit-per-pixel greyscale image with a 16-byte header containing the file magic (`61 0A 59 26`), version, image dimensions, and image data size. The converter validates all header fields before decoding.

**Usage**

```
python pm3585_to_png.py <input_file> [output.png]
```

If no output filename is given, `.png` is appended to the input filename.

---

### `pm3585_to_vcd.py` — Measurement file to VCD / CSV

Converts PM3585 logic analyzer measurement files (format versions 3 and 4) to [VCD](https://en.wikipedia.org/wiki/Value_change_dump) or CSV, suitable for viewing in [PulseView](https://sigrok.org/wiki/PulseView) / [sigrok](https://sigrok.org/).

Supports timing mode, state mode, timing+glitch, and state+timing captures. Signal names are read from the label/clock descriptors stored in the file's settings section where available, falling back to generic `podN_chN` names.

**Usage**

```
python pm3585_to_vcd.py <input_file> [options]
```

| Option | Description |
|---|---|
| `-o FILE` | Output file (default: input filename with `.vcd` or `.csv` extension) |
| `-f vcd\|csv` | Output format (default: `vcd`) |
| `--ref` | Convert the REF measurement instead of the NEW measurement |
| `--timing` | Force timing data for mode-3 (State+Timing) captures |
| `--buses` | Group multi-bit labels into VCD bus signals (experimental; may not display correctly in PulseView) |
| `--info` | Print file information and exit without converting |
| `-v` | Verbose output (sample counts, pod connections, label list) |

**Examples**

```sh
# Convert to VCD (open in PulseView)
python pm3585_to_vcd.py measurement.mea

# Convert to CSV
python pm3585_to_vcd.py measurement.mea -f csv

# Show file information
python pm3585_to_vcd.py measurement.mea --info --verbose

# Convert the REF capture to a named VCD file
python pm3585_to_vcd.py measurement.mea --ref -o ref_capture.vcd
```

## Requirements

- Python 3.8+
- [Pillow](https://python-pillow.org/) (`pip install pillow`) — required by `pm3585_to_png.py` only

## License

This project is licensed under the GNU General Public License v2.0 only.
See `LICENSE.md` for the full license text.

## File format references

- Hardcopy format: Philips PM3585 User Guide
- Measurement format: `MEAS_3_4.MAN` (distributed on PM3585 System Disk 2.0, October 7 1992)
