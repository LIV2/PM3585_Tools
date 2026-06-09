#!/usr/bin/env python3
"""
PM3585 diskette file manager

List files on the instrument diskette, download files to the local machine,
and upload local files to the instrument diskette via RS-232.

Requires the RS-232 remote control option (PF8653/30) to be installed and
enabled on the analyzer (I/O menu → Remote: RS-232, Data: 8 bit).
"""

import argparse
import os
import sys
from pathlib import Path

import pm3585_serial as la

import re

_83_RE = re.compile(r'^[A-Z0-9_$%\'`\-@{}~!#()\^&]{1,8}(\.[A-Z0-9_$%\'`\-@{}~!#()\^&]{1,3})?$')


def _check_remote_name(name: str) -> None:
    """Raise ValueError if name is not a valid DOS 8.3 filename."""
    upper = name.upper()
    if not _83_RE.match(upper):
        raise ValueError(
            f"Invalid remote filename '{name}': must be a DOS 8.3 name "
            f"(up to 8 base characters, optional dot + up to 3 extension, "
            f"ASCII letters/digits and DOS special characters only)"
        )


def cmd_ls(port, args):
    entries = la.catalogue(port, verbose=args.verbose)

    used = free = None
    files = []
    for name, size in entries:
        if name == '__used__':
            used = size
        elif name == '__free__':
            free = size
        else:
            files.append((name, size))

    if not files:
        print("(no files on diskette)")
    else:
        name_w = max(len(n) for n, _ in files)
        for name, size in files:
            print(f"  {name:<{name_w}}  {size:>8} bytes")

    if used is not None and free is not None:
        total = used + free
        print(f"\n  {used} bytes used, {free} bytes free"
              + (f" ({total} bytes total)" if total else ""))


def cmd_get(port, args):
    remote = args.remote_file
    _check_remote_name(remote)
    local = Path(args.local_file) if args.local_file else Path(remote)

    if local.exists() and not args.force:
        print(f"Error: {local} already exists. Use --force to overwrite.",
              file=sys.stderr)
        sys.exit(1)

    print(f"Downloading '{remote}' → {local} ...")
    data = la.transfer_get(port, remote, verbose=args.verbose)
    print(f"Received {len(data)} bytes")
    local.write_bytes(data)
    print(f"Saved: {local}")


def cmd_put(port, args):
    local = Path(args.local_file)
    if not local.is_file():
        print(f"Error: local file not found: {local}", file=sys.stderr)
        sys.exit(1)

    remote = args.remote_file or local.name
    _check_remote_name(remote)

    data = local.read_bytes()
    print(f"Uploading {local} ({len(data)} bytes) → '{remote}' ...")
    la.transfer_put(port, remote, data, verbose=args.verbose)
    print("Upload complete.")


def cmd_delete(port, args):
    _check_remote_name(args.remote_file)
    print(f"Deleting '{args.remote_file}' ...")
    la.send_command(port, f':MMEMory:DELete "{args.remote_file}"',
                    verbose=args.verbose)
    print("Done.")


def cmd_copy(port, args):
    _check_remote_name(args.src)
    _check_remote_name(args.dst)
    print(f"Copying '{args.src}' → '{args.dst}' ...")
    la.send_command(port, f':MMEMory:COPY "{args.src}","{args.dst}"',
                    verbose=args.verbose)
    print("Done.")


def cmd_move(port, args):
    _check_remote_name(args.src)
    _check_remote_name(args.dst)
    print(f"Renaming '{args.src}' → '{args.dst}' ...")
    la.send_command(port, f':MMEMory:MOVE "{args.src}","{args.dst}"',
                    verbose=args.verbose)
    print("Done.")


def build_parser():
    parser = argparse.ArgumentParser(
        description="PM3585 diskette file manager via RS-232",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List files on diskette
  %(prog)s /dev/tty.usbserial-110 ls

  # Download a measurement file
  %(prog)s /dev/tty.usbserial-110 get MEAS.001

  # Download with a specific local name
  %(prog)s /dev/tty.usbserial-110 get MEAS.001 capture.mea

  # Upload a file to diskette
  %(prog)s /dev/tty.usbserial-110 put capture.mea MEAS.002

  # Delete a file from diskette
  %(prog)s /dev/tty.usbserial-110 delete MEAS.002

  # Copy / rename a file on diskette
  %(prog)s /dev/tty.usbserial-110 copy MEAS.001 MEAS.BAK
  %(prog)s /dev/tty.usbserial-110 move MEAS.BAK MEAS.OLD
        """
    )

    parser.add_argument("port",
                        help="Serial port (e.g. /dev/ttyUSB0, COM3)")
    parser.add_argument("--baud", type=int, default=19200,
                        choices=[75, 150, 300, 1200, 2400, 4800, 9600, 19200],
                        help="Baud rate (default: 19200)")
    parser.add_argument("--no-handshake", action="store_true",
                        help="Disable RTS/CTS hardware handshaking")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose output")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # ls
    sub.add_parser("ls", help="List files on diskette")

    # get
    p_get = sub.add_parser("get", help="Download a file from diskette")
    p_get.add_argument("remote_file", metavar="REMOTE_FILE",
                       help="Filename on the instrument diskette")
    p_get.add_argument("local_file", metavar="LOCAL_FILE", nargs="?",
                       default=None,
                       help="Local destination path (default: same as REMOTE_FILE)")
    p_get.add_argument("-f", "--force", action="store_true",
                       help="Overwrite local file if it already exists")

    # put
    p_put = sub.add_parser("put", help="Upload a file to diskette")
    p_put.add_argument("local_file", metavar="LOCAL_FILE",
                       help="Local file to upload")
    p_put.add_argument("remote_file", metavar="REMOTE_FILE", nargs="?",
                       default=None,
                       help="Filename on the instrument diskette "
                            "(default: local filename, max 12 chars)")

    # delete
    p_del = sub.add_parser("delete", help="Delete a file from diskette",
                            aliases=["del", "rm"])
    p_del.add_argument("remote_file", metavar="REMOTE_FILE")

    # copy
    p_copy = sub.add_parser("copy", help="Copy a file on diskette",
                             aliases=["cp"])
    p_copy.add_argument("src", metavar="SRC")
    p_copy.add_argument("dst", metavar="DST")

    # move / rename
    p_move = sub.add_parser("move", help="Rename/move a file on diskette",
                             aliases=["mv", "rename"])
    p_move.add_argument("src", metavar="SRC")
    p_move.add_argument("dst", metavar="DST")

    return parser


COMMANDS = {
    "ls":     cmd_ls,
    "get":    cmd_get,
    "put":    cmd_put,
    "delete": cmd_delete,
    "del":    cmd_delete,
    "rm":     cmd_delete,
    "copy":   cmd_copy,
    "cp":     cmd_copy,
    "move":   cmd_move,
    "mv":     cmd_move,
    "rename": cmd_move,
}


def main():
    parser = build_parser()
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
        else:
            print("Warning: no IDN response; check port settings",
                  file=sys.stderr)

        COMMANDS[args.command](port, args)

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
