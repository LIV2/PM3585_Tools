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
        description="Convert a PM3585 hardcopy file to PNG format."
    )
    parser.add_argument(
        "infile",
        help="Input PM3585 hardcopy file to convert."
    )
    parser.add_argument(
        "outfile",
        nargs="?",
        default=None,
        help="Optional output PNG filename. Defaults to infile + .png."
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
    infile = args.infile
    outfile = args.outfile or infile + ".png"

    if not os.path.isfile(infile):
        print(f"Error: input file not found: {infile}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(infile, "rb") as fh:
            file_data = fh.read()

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
        print(f"Error: failed to convert {infile}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
