#!/usr/bin/env python3
"""
PM3585 RS-232 capture tool

Saves the current measurement or screen hardcopy from a Philips PM3580/PM3585
logic analyzer to a local file via RS-232.

Measurement files can be converted with pm3585_to_vcd.py.
Screen hardcopy files can be converted with pm3585_to_png.py.

Serial communication is handled by pm3585_serial.py.
"""

import argparse
import sys
import time
from pathlib import Path

import pm3585_serial as la

MEASUREMENT_MAGIC = bytes([0x71, 0x76, 0x82, 0x41])
HARDCOPY_MAGIC    = bytes([0x61, 0x0A, 0x59, 0x26])

ACQUISITION_TIMEOUT = 120


def check_file(data: bytes, screen: bool) -> bool:
    magic = HARDCOPY_MAGIC if screen else MEASUREMENT_MAGIC
    if len(data) < 6:
        return False
    if data[:4] != magic:
        return False
    if not screen:
        return ((data[4] << 8) | data[5]) in (3, 4)
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Capture PM3585 measurement or screen hardcopy via RS-232",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Capture current NEW measurement
  %(prog)s /dev/tty.usbserial-110 capture.mea

  # Trigger a new acquisition then capture
  %(prog)s /dev/tty.usbserial-110 capture.mea --trigger

  # Capture REFerence measurement
  %(prog)s /dev/tty.usbserial-110 ref.mea --ref

  # Capture screen hardcopy
  %(prog)s /dev/tty.usbserial-110 screen.hcp --screen

  # Convert captured files
  python pm3585_to_vcd.py capture.mea
  python pm3585_to_png.py screen.hcp
        """
    )
    parser.add_argument("port",
                        help="Serial port (e.g. /dev/ttyUSB0, COM3)")
    parser.add_argument("output",
                        help="Output file path")
    parser.add_argument("--baud", type=int, default=19200,
                        choices=[75, 150, 300, 1200, 2400, 4800, 9600, 19200],
                        help="Baud rate (default: 19200)")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--screen", action="store_true",
                      help="Capture screen hardcopy instead of measurement data")
    mode.add_argument("--ref", action="store_true",
                      help="Capture REFerence measurement instead of NEW")

    parser.add_argument("--trigger", action="store_true",
                        help="Send :INITiate to start a new acquisition, wait "
                             "for completion, then capture (measurement only)")
    parser.add_argument("--stop", action="store_true",
                        help="Send :STOP (manual trigger) before capturing "
                             "(measurement only)")
    parser.add_argument("--no-handshake", action="store_true",
                        help="Disable RTS/CTS hardware handshaking (use if "
                             "your cable does not wire RTS/CTS)")
    parser.add_argument("--timeout", type=float, default=ACQUISITION_TIMEOUT,
                        metavar="SECS",
                        help=f"Acquisition wait timeout in seconds "
                             f"(default: {ACQUISITION_TIMEOUT})")
    parser.add_argument("--no-check", action="store_true",
                        help="Skip the file format compatibility check")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose output")

    args = parser.parse_args()

    rtscts = not args.no_handshake
    print(f"Opening {args.port} at {args.baud} baud "
          f"({'RTS/CTS' if rtscts else 'no handshake'}) ...")

    try:
        port = la.open_port(args.port, baud=args.baud, rtscts=rtscts)
    except Exception as e:
        print(f"Error opening serial port: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        idn = la.identify(port, verbose=args.verbose)
        if idn:
            print(f"Instrument: {idn}")
            if 'PM358' not in idn.upper() and 'PHILIPS' not in idn.upper():
                print("Warning: unexpected IDN response — continuing anyway",
                      file=sys.stderr)
        else:
            print("Warning: no IDN response; check port settings", file=sys.stderr)

        if args.screen:
            print("Capturing screen hardcopy ...")
            file_data = la.capture_screen(port, verbose=args.verbose)
        else:
            if args.trigger:
                print("Starting acquisition (:INITiate) ...")
                la.send_command(port, ":INITiate", verbose=args.verbose)
                time.sleep(0.2)

            if args.stop:
                print("Sending :STOP ...")
                la.send_command(port, ":STOP", verbose=args.verbose)
                time.sleep(0.2)

            if args.trigger:
                print(f"Waiting for acquisition to complete "
                      f"(timeout {args.timeout:.0f}s) ...")
                if not la.wait_for_idle(port, timeout=args.timeout,
                                        verbose=args.verbose):
                    print("Error: acquisition did not complete within timeout",
                          file=sys.stderr)
                    sys.exit(1)
                print("Acquisition complete.")

            meas_name = "REFerence" if args.ref else "NEW"
            print(f"Dumping {meas_name} measurement ...")
            file_data = la.dump_measurement(port, use_ref=args.ref,
                                            verbose=args.verbose)

        print(f"Received {len(file_data)} bytes")

        if not args.no_check:
            if check_file(file_data, args.screen):
                fmt = ("PM3585 hardcopy file, compatible with pm3585_to_png.py"
                       if args.screen else
                       "PM3585 measurement file, compatible with pm3585_to_vcd.py")
                print(f"Format check: OK ({fmt})")
            else:
                print("Warning: received data does not look like a valid "
                      "PM3585 file.", file=sys.stderr)
                print(f"  Got: {file_data[:8].hex() if file_data else '(empty)'}",
                      file=sys.stderr)
                print("  File will still be written; use --no-check to suppress.",
                      file=sys.stderr)

        output_path = Path(args.output)
        output_path.write_bytes(file_data)
        print(f"Saved: {output_path}")

        converter = "pm3585_to_png.py" if args.screen else "pm3585_to_vcd.py"
        print(f"\nTo convert: python {converter} {output_path}")

    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        port.close()


if __name__ == "__main__":
    main()
