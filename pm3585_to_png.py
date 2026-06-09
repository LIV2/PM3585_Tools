#!/usr/bin/env python3
import argparse
import os
import struct
import sys
from PIL import Image

MAGIC = bytes([0x61, 0x0A, 0x59, 0x26])
HEADER_SIZE = 16
SUPPORTED_VERSION = 0
SUPPORTED_BPP = 2


def parse_args():
    parser = argparse.ArgumentParser(
        description="Convert a PM3585 hardcopy file to PNG format, or capture "
                    "directly from the instrument via RS-232."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "infile",
        nargs="?",
        help="Input PM3585 hardcopy file to convert."
    )
    source.add_argument(
        "--port",
        metavar="PORT",
        help="Capture screen directly from the instrument via RS-232 "
             "(e.g. /dev/ttyUSB0 or COM3)."
    )
    parser.add_argument(
        "-o", "--output",
        metavar="FILE",
        default=None,
        help="Output PNG filename. Defaults to infile + .png, or screen.png "
             "when using --port."
    )
    parser.add_argument(
        "--baud", type=int, default=19200,
        choices=[75, 150, 300, 1200, 2400, 4800, 9600, 19200],
        help="Baud rate for RS-232 (default: 19200, --port only)"
    )
    parser.add_argument(
        "--no-handshake", action="store_true",
        help="Disable RTS/CTS handshaking (--port only)"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Verbose output (--port only)"
    )
    return parser.parse_args()


def parse_header(data):
    if len(data) < HEADER_SIZE:
        raise ValueError(
            f"File too short: expected at least {HEADER_SIZE} bytes, got {len(data)}"
        )

    magic = data[0:4]
    if magic != MAGIC:
        raise ValueError(
            f"Invalid file magic: expected {MAGIC.hex()} got {magic.hex()}"
        )

    # All multi-byte fields are big-endian (first byte = most significant)
    version = struct.unpack_from(">H", data, 4)[0]
    if version != SUPPORTED_VERSION:
        raise ValueError(
            f"Unsupported file version: {version} (expected {SUPPORTED_VERSION})"
        )

    pixels_per_line = struct.unpack_from(">H", data, 6)[0]
    scan_lines      = struct.unpack_from(">H", data, 8)[0]
    bpp             = struct.unpack_from(">H", data, 10)[0]
    image_size      = struct.unpack_from(">I", data, 12)[0]

    if bpp != SUPPORTED_BPP:
        raise ValueError(
            f"Unsupported bits-per-pixel: {bpp} (expected {SUPPORTED_BPP})"
        )
    if pixels_per_line == 0 or scan_lines == 0:
        raise ValueError(
            f"Invalid dimensions: {pixels_per_line}x{scan_lines}"
        )

    # Each byte encodes (8 / bpp) = 4 pixels
    pixels_per_byte = 8 // bpp
    expected_size = (pixels_per_line * scan_lines + pixels_per_byte - 1) // pixels_per_byte
    if image_size != expected_size:
        raise ValueError(
            f"Image size mismatch: header says {image_size} bytes, "
            f"expected {expected_size} for {pixels_per_line}x{scan_lines} at {bpp} bpp"
        )

    return pixels_per_line, scan_lines, bpp, image_size


def decode_image(raw_bytes, pixels_per_line, scan_lines, bpp):
    clut = [
        (0xFF, 0xFF, 0xFF),
        (0xCC, 0xCC, 0xCC),
        (0xAA, 0xAA, 0xAA),
        (0x00, 0x00, 0x00),
    ]

    mask = (1 << bpp) - 1
    total_pixels = pixels_per_line * scan_lines

    pixarray = []
    pixel_count = 0
    for byte in raw_bytes:
        for shift in range(8 - bpp, -1, -bpp):
            if pixel_count >= total_pixels:
                break
            index = (byte >> shift) & mask
            pixarray.extend(clut[index])
            pixel_count += 1

    return pixarray


def main():
    args = parse_args()

    try:
        if args.port:
            import pm3585_serial as la
            rtscts = not args.no_handshake
            print(f"Connecting to {args.port} at {args.baud} baud "
                  f"({'RTS/CTS' if rtscts else 'no handshake'}) ...")
            port = la.open_port(args.port, baud=args.baud, rtscts=rtscts)
            try:
                idn = la.identify(port, verbose=args.verbose)
                if idn:
                    print(f"Instrument: {idn}")
                print("Capturing screen ...")
                file_data = la.capture_screen(port, verbose=args.verbose)
            finally:
                port.close()
            outfile = args.output or "screen.png"
        else:
            infile = args.infile
            if not os.path.isfile(infile):
                print(f"Error: input file not found: {infile}", file=sys.stderr)
                sys.exit(1)
            with open(infile, "rb") as fh:
                file_data = fh.read()
            outfile = args.output or infile + ".png"

        pixels_per_line, scan_lines, bpp, image_size = parse_header(file_data)

        raw_bytes = file_data[HEADER_SIZE:]
        if len(raw_bytes) < image_size:
            raise ValueError(
                f"Truncated file: expected {image_size} image bytes, got {len(raw_bytes)}"
            )
        raw_bytes = raw_bytes[:image_size]

        pixarray = decode_image(raw_bytes, pixels_per_line, scan_lines, bpp)

        png = Image.frombytes("RGB", (pixels_per_line, scan_lines), bytes(pixarray))
        png.save(outfile, "PNG")
        print(f"Saved: {outfile}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
