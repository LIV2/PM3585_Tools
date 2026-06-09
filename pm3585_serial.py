#!/usr/bin/env python3
"""
PM3585 RS-232 / SCPI interface module

Provides serial port access and SCPI communication for the Philips PM3580/PM3585
logic analyzer.  Intended to be imported by pm3585_capture.py, pm3585_to_png.py
and pm3585_to_vcd.py.

RS-232 setup on the analyzer (I/O menu):
  Remote: RS-232
  Baud:   19200  (or as configured)
  Data:   8 bit  (required for binary transfer)
  Parity: None
  Stop:   1
  Handshaking: RTS/CTS (recommended) or None

Data encoding (Programming Manual chapter 12):
  All bytes sent by the instrument are ESC-encoded: any 0x1B byte is
  transmitted as 0x1B 0x1B.  The declared byte count in each IEEE-488.2
  definite-length block header is the number of *unescaped* bytes, so block
  payloads must be read byte-by-byte with inline unescaping until the declared
  count is reached.  Block headers and ASCII responses are never ESC-encoded.
  The response message terminator is 0x0A (newline).
"""

import time

try:
    import serial
except ImportError as _e:
    raise ImportError(
        "pyserial is required: pip install pyserial"
    ) from _e


ESC = 0x1B

# Bit 4 of the Operation Condition register: set while acquisition is running.
OPER_COND_MEASURING_BIT = (1 << 4)

# Temporary filename written to the analyzer diskette during screen capture.
SCREEN_TEMP_NAME = "SCPI_HCP"


# ---------------------------------------------------------------------------
# Port helpers
# ---------------------------------------------------------------------------

def open_port(port: str, baud: int = 19200, rtscts: bool = True) -> serial.Serial:
    """
    Open and return a configured serial port.

    Raises serial.SerialException if the port cannot be opened.
    """
    return serial.Serial(
        port=port,
        baudrate=baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        rtscts=rtscts,
        timeout=5.0,
    )


def _cts_hint(port: serial.Serial) -> str:
    """
    If the port is in RTS/CTS mode and CTS is deasserted, return a hint string
    to append to timeout error messages. Otherwise return an empty string.
    """
    try:
        if port.rtscts and not port.getCTS():
            return (
                " (CTS is deasserted — instrument may not be ready or "
                "the cable may not wire RTS/CTS; try --no-handshake)"
            )
    except Exception:
        pass
    return ""


def _read_exact(port: serial.Serial, n: int) -> bytes:
    """Read exactly n raw bytes, raising IOError on timeout."""
    buf = b''
    while len(buf) < n:
        chunk = port.read(n - len(buf))
        if not chunk:
            raise IOError(
                f"Serial read timeout: got {len(buf)} of {n} expected bytes"
                + _cts_hint(port))
        buf += chunk
    return buf


# ---------------------------------------------------------------------------
# SCPI command / query
# ---------------------------------------------------------------------------

def send_command(port: serial.Serial, cmd: str, verbose: bool = False) -> None:
    """Send a SCPI command terminated by newline."""
    msg = cmd.strip() + '\n'
    if verbose:
        print(f"  -> {cmd!r}")
    port.write(msg.encode('ascii'))
    port.flush()


def read_ascii_line(port: serial.Serial, timeout: float = 5.0) -> str:
    """Read one ASCII response line, stripping the newline terminator."""
    port.timeout = timeout
    line = port.readline()
    if not line:
        raise IOError("Serial read timeout waiting for response"
                      + _cts_hint(port))
    return line.decode('ascii', errors='replace').strip()


def query(port: serial.Serial, cmd: str, timeout: float = 5.0,
          verbose: bool = False) -> str:
    """Send a SCPI query and return the ASCII response line."""
    send_command(port, cmd, verbose=verbose)
    response = read_ascii_line(port, timeout=timeout)
    if verbose:
        print(f"  <- {response!r}")
    return response


def identify(port: serial.Serial, verbose: bool = False) -> str:
    """Send *IDN? and return the identification string, or '' on timeout."""
    try:
        return query(port, "*IDN?", verbose=verbose)
    except IOError as e:
        if verbose:
            print(f"  [IDN timeout] {e}")
        return ""


# ---------------------------------------------------------------------------
# Binary block transfer
# ---------------------------------------------------------------------------

def read_blocks(port: serial.Serial, verbose: bool = False) -> bytes:
    """
    Read a sequence of IEEE-488.2 definite-length blocks already queued on the
    port and return the reassembled binary payload.

    The caller must have already sent the query that triggers the response.

    Response format: <block>{,<block>}<newline>
    Each block:      #<n><length_digits><payload>
      #               - literal '#'
      <n>             - one ASCII digit: number of length digits following
      <length_digits> - <n> ASCII digits: count of *unescaped* payload bytes
      <payload>       - ESC-encoded wire bytes (ESC ESC on wire = one ESC byte)

    Blocks are separated by ',' and the response ends with a bare newline.
    """
    file_data = b''
    block_count = 0

    while True:
        port.timeout = 30.0
        next_byte = port.read(1)
        if not next_byte:
            break

        b = next_byte[0]

        if b == ord('#'):
            n_raw = _read_exact(port, 1)
            n = int(chr(n_raw[0]))
            if n < 1 or n > 9:
                raise ValueError(f"Invalid block header length digit: {n}")

            length_raw = _read_exact(port, n)
            unescaped_count = int(length_raw.decode('ascii'))

            if verbose:
                print(f"  [block {block_count}] declared {unescaped_count} bytes")

            port.timeout = 60.0
            payload = bytearray()
            while len(payload) < unescaped_count:
                byte = port.read(1)
                if not byte:
                    raise IOError(
                        f"Serial read timeout in block {block_count}: "
                        f"got {len(payload)} of {unescaped_count} unescaped bytes"
                        + _cts_hint(port))
                if byte[0] == ESC:
                    next_b = port.read(1)
                    if not next_b:
                        raise IOError("Serial read timeout after ESC byte"
                                      + _cts_hint(port))
                    if next_b[0] == ESC:
                        payload.append(ESC)
                    else:
                        # Unexpected special sequence; pass both bytes through.
                        payload.append(ESC)
                        payload.append(next_b[0])
                else:
                    payload.append(byte[0])

            if verbose:
                print(f"  [block {block_count}] {len(payload)} bytes received")

            file_data += payload
            block_count += 1

        elif b in (ord(','), ord('\r')):
            continue

        elif b == ord('\n'):
            break

        else:
            if verbose:
                print(f"  [skip byte] 0x{b:02x}")

    if block_count == 0:
        raise ValueError("No data blocks received from instrument")

    if verbose:
        print(f"  Total: {block_count} block(s), {len(file_data)} bytes")

    return file_data


def _encode_block(data: bytes) -> bytes:
    """
    Encode binary data as a single IEEE-488.2 definite-length block with
    ESC-encoding applied to the payload (mirrors what the instrument sends).

    Format: #<n><length_digits><esc_encoded_payload>
    The length digits give the *unescaped* byte count.
    """
    escaped = bytearray()
    for b in data:
        escaped.append(b)
        if b == ESC:
            escaped.append(ESC)
    length_str = str(len(data))
    header = f"#{len(length_str)}{length_str}".encode('ascii')
    return header + bytes(escaped)


def catalogue(port: serial.Serial, verbose: bool = False) -> list[tuple[str, int]]:
    """
    Query :MMEMory:CATalogue? and return a list of (filename, size) tuples.

    Response format: <used>,<free>,"<name>,,<size>","<name>,,<size>",...
    Each file entry is a quoted string with three comma-separated fields:
    name, type (always empty), size.

    Also returns the disk summary as the first two pseudo-entries with names
    '__used__' and '__free__' (sizes in bytes) so callers can show disk usage.
    """
    import csv, io as _io
    response = query(port, ":MMEMory:CATalogue?", timeout=15.0, verbose=verbose)

    # Use csv.reader to correctly handle quoted fields containing commas.
    tokens = next(csv.reader(_io.StringIO(response.strip())))

    entries: list[tuple[str, int]] = []
    i = 0

    if len(tokens) >= 2:
        try:
            entries.append(('__used__', int(tokens[0].strip())))
            entries.append(('__free__', int(tokens[1].strip())))
        except ValueError:
            pass
        i = 2

    # Each remaining token is "name,,size" (type field is always empty).
    while i < len(tokens):
        fields = tokens[i].split(',')
        i += 1
        name = fields[0].strip()
        if not name:
            continue
        try:
            size = int(fields[2].strip()) if len(fields) >= 3 else 0
        except ValueError:
            size = 0
        entries.append((name, size))

    return entries


def transfer_get(port: serial.Serial, filename: str,
                 verbose: bool = False) -> bytes:
    """Download a file from the instrument diskette over RS-232."""
    send_command(port, f':MMEMory:TRANsfer? "{filename}"', verbose=verbose)
    return read_blocks(port, verbose=verbose)


def transfer_put(port: serial.Serial, filename: str, data: bytes,
                 verbose: bool = False) -> None:
    """
    Upload data to a file on the instrument diskette over RS-232.

    Sends: :MMEMory:TRANsfer "<filename>",<block>\n
    """
    block = _encode_block(data)
    cmd_prefix = f':MMEMory:TRANsfer "{filename}",'.encode('ascii')
    msg = cmd_prefix + block + b'\n'
    if verbose:
        print(f"  -> :MMEMory:TRANsfer \"{filename}\",<{len(data)} bytes>")
    port.write(msg)
    port.flush()


# ---------------------------------------------------------------------------
# High-level capture operations
# ---------------------------------------------------------------------------

def dump_measurement(port: serial.Serial, use_ref: bool = False,
                     verbose: bool = False) -> bytes:
    """
    Issue :MEMory:DUMP:MEASurement? and return the binary measurement file.

    If use_ref is True, the REFerence measurement is returned instead of NEW.
    If the acquisition is still running when NEW is requested, the instrument
    waits for it to complete before responding.
    """
    param = " REFerence" if use_ref else ""
    send_command(port, f":MEMory:DUMP:MEASurement?{param}", verbose=verbose)
    return read_blocks(port, verbose=verbose)


def capture_screen(port: serial.Serial, verbose: bool = False) -> bytes:
    """
    Capture the current screen image and return it as a hardcopy file.

    There is no command to stream the screen directly over RS-232, so this
    works in three steps:
      1. :MMEMory:STORe:SCReen  — write screen image to a temp file on diskette
      2. :MMEMory:TRANsfer?     — read the file back over RS-232
      3. :MMEMory:DELete        — remove the temp file

    The delete runs in a finally block so the diskette is not left with stale
    files even if the transfer fails.
    """
    tmp = SCREEN_TEMP_NAME

    if verbose:
        print(f"  Storing screen to diskette as '{tmp}' ...")
    send_command(port, f':MMEMory:STORe:SCReen "{tmp}"', verbose=verbose)
    time.sleep(1.0)

    try:
        send_command(port, f':MMEMory:TRANsfer? "{tmp}"', verbose=verbose)
        return read_blocks(port, verbose=verbose)
    finally:
        try:
            send_command(port, f':MMEMory:DELete "{tmp}"', verbose=verbose)
            time.sleep(0.5)
        except Exception as e:
            import sys
            print(f"Warning: could not delete '{tmp}' from diskette: {e}",
                  file=sys.stderr)


def wait_for_idle(port: serial.Serial, timeout: float = 120,
                  verbose: bool = False) -> bool:
    """
    Poll :STATus:OPERation:CONDition? until bit 4 (measuring) clears.
    Returns True if IDLE was reached within timeout seconds, False otherwise.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = query(port, ":STATus:OPERation:CONDition?", verbose=verbose)
        try:
            if response.startswith(('#B', '#b')):
                condition = int(response[2:], 2)
            elif response.startswith(('0x', '0X')):
                condition = int(response, 16)
            else:
                condition = int(response)
        except ValueError:
            if verbose:
                print(f"  [warn] Could not parse condition register: {response!r}")
            time.sleep(1.0)
            continue

        if not (condition & OPER_COND_MEASURING_BIT):
            return True
        time.sleep(0.5)

    return False
