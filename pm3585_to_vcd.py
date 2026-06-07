#!/usr/bin/env python3
"""
PM3585 Logic Analyzer Measurement File to Sigrok VCD Converter

Converts Philips PM3580/3585 measurement files (version 3 & 4) to VCD format
for use with sigrok/PulseView.

Based on MEAS_3_4.MAN documentation (October 7, 1992)
"""

import sys
import argparse
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import BinaryIO


# File magic bytes
FILE_MAGIC = bytes([0x71, 0x76, 0x82, 0x41])

# Time unit: 5ns (timestamps are in 5ns units)
TIME_UNIT_NS = 5

# Reference epoch: January 1, 1976 00:00:00
EPOCH_1976 = datetime(1976, 1, 1)


@dataclass
class FileHeader:
    """PM3585 file header structure"""
    version: int = 0
    flags: int = 0
    hw_config: int = 0
    sw_config: int = 0
    offset_new: int = 0
    offset_ref: int = 0
    offset_settings: int = 0
    production_time: int = 0

    @property
    def frequency_mhz(self) -> int:
        """Get frequency from config (100 or 200 MHz)"""
        return 200 if (self.hw_config & 0x01) else 100

    @property
    def num_pods(self) -> int:
        """Number of pods (1-6)"""
        return ((self.hw_config >> 1) & 0x07)

    @property
    def num_analyzers(self) -> int:
        """Number of analyzers (1-2)"""
        return ((self.hw_config >> 4) & 0x03)


@dataclass
class PodAcquisition:
    """Pod acquisition data"""
    connection: int = 2  # 0=Analyzer1, 1=Analyzer2, 2=Not connected
    timing_channel_alloc: int = 0
    state_channel_alloc: int = 0
    timing_data: list = field(default_factory=list)
    state_data: list = field(default_factory=list)
    glitch_data: list = field(default_factory=list)


@dataclass
class AcquisitionStatus:
    """Acquisition status block"""
    status_codes: list = field(default_factory=list)
    timestamps: list = field(default_factory=list)
    trigger_time: int = 0
    trigger_sample: int = 0
    num_samples: int = 0


@dataclass
class AnalyzerAcquisition:
    """Analyzer acquisition data"""
    mode: int = 4  # 0=Timing, 1=State, 2=Timing+Glitch, 3=State+Timing, 4=Off
    clock_channels: list = field(default_factory=lambda: [96, 96, 96, 96])
    qualifiers: list = field(default_factory=list)
    timing_status: AcquisitionStatus = field(default_factory=AcquisitionStatus)
    state_status: AcquisitionStatus = field(default_factory=AcquisitionStatus)


@dataclass
class Measurement:
    """Complete measurement data"""
    config: int = 0
    meas_time: int = 0
    pods: list = field(default_factory=list)
    analyzers: list = field(default_factory=list)


@dataclass
class LabelDescriptor:
    """Label descriptor from settings"""
    name: str = ""
    flags: int = 0
    channel_count: int = 0
    channels: list = field(default_factory=list)  # List of channel numbers (0-95)
    format: int = 0

    @property
    def is_timing_label(self) -> bool:
        """Check if this is a timing label"""
        return bool(self.flags & 0x04)

    @property
    def polarity_positive(self) -> bool:
        """Check if polarity is positive"""
        return bool(self.flags & 0x02)


@dataclass
class ClockDescriptor:
    """Clock descriptor from settings"""
    name: str = ""
    flags: int = 0
    channel: int = 96  # 96 = not assigned
    edge: int = 0
    on_same_line_as: int = 0


@dataclass
class AnalyzerSettings:
    """Analyzer settings from file"""
    name: str = ""
    flags: int = 0
    pod_allocation: int = 0
    clocks: list = field(default_factory=list)   # List of ClockDescriptor
    labels: list = field(default_factory=list)   # List of LabelDescriptor


@dataclass
class Settings:
    """User settings from file"""
    analyzers: list = field(default_factory=list)  # List of AnalyzerSettings


class PM3585Reader:
    """Reader for PM3585 measurement files"""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.file: BinaryIO = None
        self.header: FileHeader = None
        self.new_measurement: Measurement = None
        self.ref_measurement: Measurement = None
        self.settings: Settings = None

    def read_uint1(self) -> int:
        """Read unsigned 1-byte integer"""
        data = self.file.read(1)
        if len(data) < 1:
            raise EOFError("Unexpected end of file")
        return data[0]

    def read_uint2(self) -> int:
        """Read unsigned 2-byte integer (big endian)"""
        data = self.file.read(2)
        if len(data) < 2:
            raise EOFError("Unexpected end of file")
        return (data[0] << 8) | data[1]

    def read_uint4(self) -> int:
        """Read unsigned 4-byte integer (big endian)"""
        data = self.file.read(4)
        if len(data) < 4:
            raise EOFError("Unexpected end of file")
        return (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3]

    def read_chan2(self) -> int:
        """Read CHAN2 (16-bit channel allocation)"""
        return self.read_uint2()

    def read_chan12(self) -> bytes:
        """Read CHAN12 (96-bit channel allocation)"""
        return self.file.read(12)

    def read_string(self) -> str:
        """Read null-terminated string"""
        chars = []
        while True:
            byte = self.file.read(1)
            if len(byte) < 1 or byte[0] == 0:
                break
            chars.append(chr(byte[0]))
        return ''.join(chars)

    def read_bytes(self, n: int) -> bytes:
        """Read n bytes"""
        return self.file.read(n)

    def read_stamp(self) -> int:
        """Read variable-length timestamp"""
        first_byte = self.read_uint1()

        # Extract sign (bit 3)
        is_negative = bool(first_byte & 0x08)

        # Extract length (bits 4-7) + 1
        stamp_length = ((first_byte >> 4) & 0x0F) + 1

        # Start with bits 0-2 of first byte
        value = first_byte & 0x07

        # Read remaining bytes
        for _ in range(1, stamp_length):
            value = (value << 8) | self.read_uint1()

        if is_negative:
            value = -value

        return value

    def read_header(self) -> FileHeader:
        """Read and validate file header"""
        # Check magic
        magic = self.file.read(4)
        if magic != FILE_MAGIC:
            raise ValueError(f"Invalid file magic: {magic.hex()}, expected {FILE_MAGIC.hex()}")

        header = FileHeader()
        header.version = self.read_uint2()

        if header.version not in (3, 4):
            raise ValueError(f"Unsupported file version: {header.version}")

        header.flags = self.read_uint2()
        header.hw_config = self.read_uint1()
        header.sw_config = self.read_uint1()
        header.offset_new = self.read_uint4()
        header.offset_ref = self.read_uint4()
        header.offset_settings = self.read_uint4()
        header.production_time = self.read_uint4()

        # Skip reserved bytes
        self.file.read(2)

        return header

    def read_pod_acquisition(self) -> PodAcquisition:
        """Read pod acquisition block"""
        pod = PodAcquisition()
        pod.connection = self.read_uint1()

        if pod.connection == 2:
            # Pod not connected, no more data
            return pod

        pod.timing_channel_alloc = self.read_chan2()
        pod.state_channel_alloc = self.read_chan2()

        # Read timing data
        timing_entries = self.read_uint2()
        pod.timing_data = [self.read_chan2() for _ in range(timing_entries)]

        # Read state data
        state_entries = self.read_uint2()
        pod.state_data = [self.read_chan2() for _ in range(state_entries)]

        # Read glitch data
        glitch_entries = self.read_uint2()
        pod.glitch_data = [self.read_chan2() for _ in range(glitch_entries)]

        return pod

    def read_acquisition_status(self, is_timing: bool) -> AcquisitionStatus:
        """Read acquisition status block"""
        status = AcquisitionStatus()

        num_entries = self.read_uint2()
        if num_entries == 0:
            return status

        # Read status codes (2 bytes for timing, 1 byte for state)
        if is_timing:
            status.status_codes = [self.read_uint2() for _ in range(num_entries)]
        else:
            status.status_codes = [self.read_uint1() for _ in range(num_entries)]

        # Read timestamps
        status.timestamps = [self.read_stamp() for _ in range(num_entries)]

        # Read trigger info
        status.trigger_time = self.read_stamp()
        status.trigger_sample = self.read_uint2()
        status.num_samples = self.read_uint2()

        return status

    def read_analyzer_acquisition(self) -> AnalyzerAcquisition:
        """Read analyzer acquisition block"""
        analyzer = AnalyzerAcquisition()
        analyzer.mode = self.read_uint1()

        if analyzer.mode == 4:
            # Analyzer off, no more data
            return analyzer

        # Read clock channels
        analyzer.clock_channels = [self.read_uint1() for _ in range(4)]

        # Read 4 qualifier settings
        for _ in range(4):
            clock_num = self.read_uint1()
            if clock_num != 4:
                # Qualifier connected, read channel allocation and levels
                self.read_chan12()  # channel allocation
                self.read_chan12()  # channel levels

        # Read timing acquisition status
        analyzer.timing_status = self.read_acquisition_status(is_timing=True)

        # Read state acquisition status
        analyzer.state_status = self.read_acquisition_status(is_timing=False)

        return analyzer

    def read_measurement(self) -> Measurement:
        """Read a complete measurement"""
        meas = Measurement()

        # Read measurement header
        meas.config = self.read_uint1()
        meas.meas_time = self.read_uint4()

        # Read 6 pod acquisition blocks
        meas.pods = [self.read_pod_acquisition() for _ in range(6)]

        # Read 2 analyzer acquisition blocks
        meas.analyzers = [self.read_analyzer_acquisition() for _ in range(2)]

        return meas

    def read_label_descriptor(self) -> LabelDescriptor:
        """Read a label descriptor from settings"""
        label = LabelDescriptor()
        label.name = self.read_string()
        label.flags = self.read_uint2()
        label.channel_count = self.read_uint1()

        # Read channel allocation
        if label.channel_count > 0:
            if label.channel_count < 12:
                # Each channel is specified in a separate byte
                label.channels = [self.read_uint1() for _ in range(label.channel_count)]
            else:
                # CHAN12 structure - each bit specifies a channel
                # MSB of chan12[0] = channel 95, LSB of chan12[11] = channel 0
                chan12 = self.read_chan12()
                label.channels = []
                for byte_idx, byte in enumerate(chan12):
                    for bit in range(8):
                        if byte & (0x80 >> bit):
                            channel = 95 - (byte_idx * 8 + bit)
                            label.channels.append(channel)
                # Sort channels in ascending order for proper bit indexing
                label.channels.sort()

        label.format = self.read_uint1()

        # Version 4 has additional symbol fields for all labels
        if self.header.version == 4:
            self.read_uint1()  # Symbol Viewsize
            self.read_uint1()  # Symbol Format
            self.read_uint1()  # Max Name Length
            self.read_uint1()  # Unique Name Length
            self.read_uint1()  # Max Range Offset Width
            self.read_uint2()  # First Displayed Symbol
            symbol_count = self.read_uint2()

            # Skip symbol descriptors
            for _ in range(symbol_count):
                self.read_string()  # Name
                flags = self.read_uint1()
                self.read_uint1()  # Unique Length

                # Read boundary values based on flags
                if flags & 0x40:  # Boundary 1 value saved
                    self.read_uint4()
                if flags & 0x80:  # Boundary 1 mask saved
                    self.read_uint4()
                if flags & 0x10:  # Boundary 2 value saved
                    self.read_uint4()
                if flags & 0x20:  # Boundary 2 mask saved
                    self.read_uint4()

        return label

    def read_clock_descriptor(self) -> ClockDescriptor:
        """Read a clock descriptor from settings"""
        clock = ClockDescriptor()
        clock.name = self.read_string()
        clock.flags = self.read_uint1()
        clock.channel = self.read_uint1()
        clock.edge = self.read_uint1()
        clock.on_same_line_as = self.read_uint1()
        qualifier_count = self.read_uint1()

        # Skip qualifier descriptors (24 bytes each)
        for _ in range(qualifier_count):
            self.read_bytes(24)

        return clock

    def skip_trace_descriptor(self):
        """Skip trace descriptor - complex variable length structure"""
        self.read_uint1()  # Sequence Type
        self.read_uint1()  # Data Stored
        self.read_uint1()  # Trigger position

        # User Defined State Final Delay Descriptor
        self.read_uint1()  # Delay Type
        self.read_uint4()  # Wait Value
        self.read_uint4()  # Fill Value
        self.read_uint4()  # Clock Count Value
        self.read_uint1()  # Clock Count Unit

        # User Defined Time Final Delay Descriptor
        self.read_uint1()  # Delay Type
        self.read_uint4()  # Wait Value
        self.read_uint4()  # Fill Value

        # Time Compare Descriptor - skip 8 bytes
        self.read_bytes(8)

        self.read_uint1()  # Skew

        # State Compare Descriptor - skip 8 bytes
        self.read_bytes(8)

        self.read_uint1()  # Active Analyzer
        self.read_uint1()  # Active Trace Area
        self.read_uint1()  # Active Run Definition Field
        self.read_uint1()  # Active Predefined Sequence Field
        self.read_uint1()  # Flagged Predefined Sequence Field

        # User Defined Sequence Descriptor
        self.read_uint1()  # First Displayed Line Level
        self.read_uint1()  # First Displayed Line
        self.read_uint1()  # Last Displayed Line Level
        self.read_uint1()  # Last Displayed Line
        self.read_uint1()  # Active Line Level
        self.read_uint1()  # Active Line
        self.read_uint1()  # Active Line Index

        self.read_uint1()  # Sequence Line Type
        self.read_uint1()  # First Displayed Triggerword Label
        self.read_uint1()  # First Displayed Triggerword
        self.read_uint1()  # Active Triggerword Field
        self.read_uint1()  # Active Triggerword Line
        self.read_uint1()  # Active Triggerword Label
        self.read_uint1()  # Triggerword Line Order

        tw_label_count = self.read_uint1()
        for _ in range(tw_label_count):
            self.read_uint1()  # Flags
            self.read_uint1()  # Reference
            self.read_uint1()  # Format

    def skip_sequence_descriptor(self):
        """Skip sequence descriptor"""
        # Global Store Condition
        self.read_uint1()  # Storage Type
        self.read_uint1()  # Words sw1-sw8 On/Off
        self.read_uint1()  # Words sw1-sw8 True/False
        self.read_uint1()  # Clocks On/Off
        self.read_uint1()  # Range On/Off
        self.read_uint1()  # Range True/False

        # Restart If Condition
        self.read_uint1()  # If Condition Type
        self.read_uint1()  # Words sw1-sw8 On/Off
        self.read_uint1()  # Words sw1-sw8 True/False
        self.read_uint1()  # Immediate On/Off
        self.read_uint1()  # Clocks On/Off
        self.read_uint1()  # Range On/Off
        self.read_uint1()  # Range True/False
        self.read_uint1()  # Time word On/Off
        self.read_bytes(12)  # Filterwords tw7, tw8
        self.read_uint1()  # Glitch
        self.read_uint1()  # Edge
        self.read_uint4()  # Times
        self.read_uint4()  # Timeout
        self.read_uint1()  # Trigger condition
        self.read_uint1()  # Goto

        level_count = self.read_uint1()
        for _ in range(level_count):
            # Level Store Condition (6 bytes)
            self.read_bytes(6)
            # Wait Condition (variable)
            self.read_bytes(28)
            # First If Condition (variable)
            self.read_bytes(28)
            # Second If Condition (variable)
            self.read_bytes(28)

    def skip_trigger_words(self):
        """Skip trigger words section"""
        # Edge Word (12 bytes)
        self.read_bytes(12)
        # Glitch Word (12 bytes)
        self.read_bytes(12)

        # 8 State Words
        for _ in range(8):
            self.read_uint1()  # Clock Number
            self.read_uint1()  # Mode
            self.read_uint4()  # Filter Time
            self.read_bytes(12)  # Channel Value

        # 2 Time Words
        for _ in range(2):
            self.read_uint1()  # Clock Number
            self.read_bytes(12)  # Channel Value

        # Range
        self.read_uint1()  # Clock Number
        self.read_uint1()  # Label Number
        self.read_bytes(12)  # Lower Bound
        self.read_bytes(12)  # Upper Bound

    def skip_disassembler_settings(self):
        """Skip disassembler settings"""
        flags = self.read_uint1()
        if not (flags & 0x01):  # No disassembler
            return

        param_count = self.read_uint1()
        for _ in range(param_count):
            self.read_uint2()  # Parameter ID
            self.read_uint4()  # Parameter Value

        clock_count = self.read_uint1()
        for _ in range(clock_count):
            self.read_uint1()  # Clock Index

        label_count = self.read_uint1()
        for _ in range(label_count):
            self.read_uint1()  # Label Index

    def read_analyzer_settings(self) -> AnalyzerSettings:
        """Read analyzer settings"""
        analyzer = AnalyzerSettings()
        analyzer.name = self.read_string()
        analyzer.flags = self.read_uint1()
        analyzer.pod_allocation = self.read_uint1()

        # Read clock descriptors
        clock_count = self.read_uint1()
        analyzer.clocks = [self.read_clock_descriptor() for _ in range(clock_count)]

        # Read label descriptors
        label_count = self.read_uint1()
        analyzer.labels = [self.read_label_descriptor() for _ in range(label_count)]

        # Skip remaining analyzer settings
        self.skip_trace_descriptor()
        self.skip_sequence_descriptor()
        self.skip_trigger_words()
        self.read_uint1()  # Option
        self.skip_disassembler_settings()

        return analyzer

    def read_settings(self) -> Settings:
        """Read user settings section"""
        settings = Settings()

        # Global System Settings (5 bytes)
        self.read_uint1()  # Flags
        self.read_uint4()  # Auto Repeat Time

        # Channel Settings (96 bytes - one per channel)
        self.read_bytes(96)

        # Pod Settings (6 pods)
        for _ in range(6):
            self.read_uint1()  # Threshold Type (group 1)
            if self.header.version == 3:
                self.read_uint1()  # Auto Value (version 3 only)
            self.read_uint1()  # User Defined Value (group 1)
            self.read_uint1()  # Threshold Type (group 2)
            if self.header.version == 3:
                self.read_uint1()  # Auto Value (version 3 only)
            self.read_uint1()  # User Defined Value (group 2)

        # Read Analyzer Settings (2 analyzers)
        settings.analyzers = [self.read_analyzer_settings() for _ in range(2)]

        return settings

    def read(self):
        """Read the complete file"""
        with open(self.filepath, 'rb') as f:
            self.file = f
            self.header = self.read_header()

            # Read NEW measurement if present
            if self.header.offset_new != 0:
                f.seek(self.header.offset_new)
                self.new_measurement = self.read_measurement()

            # Read REF measurement if present
            if self.header.offset_ref != 0:
                f.seek(self.header.offset_ref)
                self.ref_measurement = self.read_measurement()

            # Read settings if present
            if self.header.offset_settings != 0:
                f.seek(self.header.offset_settings)
                try:
                    self.settings = self.read_settings()
                except Exception as e:
                    # Settings parsing is complex, continue without labels if it fails
                    print(f"Warning: Could not parse settings (labels will use generic names): {e}")
                    self.settings = None

        return self


class VCDWriter:
    """Writer for VCD (Value Change Dump) files with bus support"""

    def __init__(self, output_path: str, timescale_ns: int = 5):
        self.output_path = output_path
        self.timescale_ns = timescale_ns
        self.signals: dict = {}  # (pod_idx, ch) -> vcd_id for single-bit signals
        self.buses: dict = {}    # bus_name -> (vcd_id, width, [(pod_idx, ch), ...])
        self.channel_to_label: dict = {}  # channel_num -> (label_name, bit_index)
        self.channel_to_bus: dict = {}    # channel_num -> bus_name (for multi-bit labels)
        self.current_id = ord('!')
        self.all_channels = True

    def _next_id(self) -> str:
        """Generate next VCD signal identifier"""
        id_char = chr(self.current_id)
        self.current_id += 1
        if self.current_id > ord('~'):
            raise ValueError("Too many signals for single-character VCD identifiers")
        return id_char

    def _build_channel_label_map(self, reader: PM3585Reader, analyzer_idx: int):
        """Build mapping from channel numbers to label names and buses"""
        self.channel_to_label = {}
        self.channel_to_bus = {}
        self.bus_definitions = {}  # bus_name -> list of channels (ordered by bit index)

        if reader.settings is None or analyzer_idx >= len(reader.settings.analyzers):
            return

        analyzer_settings = reader.settings.analyzers[analyzer_idx]

        # Add clock channel names
        for clock in analyzer_settings.clocks:
            if clock.name and clock.channel < 96:
                self.channel_to_label[clock.channel] = (clock.name, None)

        # Add label channel names (may override clock names)
        for label in analyzer_settings.labels:
            if not label.name or not label.channels:
                continue

            if len(label.channels) == 1:
                # Single channel label - just use the label name
                self.channel_to_label[label.channels[0]] = (label.name, None)
            else:
                # Multi-channel label - this is a bus
                # Store channels in order (bit 0 first)
                self.bus_definitions[label.name] = label.channels[:]
                for bit_idx, channel in enumerate(label.channels):
                    self.channel_to_label[channel] = (label.name, bit_idx)
                    self.channel_to_bus[channel] = label.name

    def _get_signal_name(self, pod_idx: int, channel_in_pod: int) -> str:
        """Get signal name for a channel, using label if available

        Args:
            pod_idx: Physical pod index (0-5)
            channel_in_pod: Channel within the pod (0-15)
        """
        global_channel = (pod_idx * 16) + channel_in_pod

        if global_channel in self.channel_to_label:
            label_name, bit_idx = self.channel_to_label[global_channel]
            if bit_idx is not None:
                return f"{label_name}[{bit_idx}]"
            else:
                return label_name

        # Fallback to generic name
        return f"pod{pod_idx + 1}_ch{channel_in_pod}"

    def write(self, reader: PM3585Reader, use_ref: bool = False, force_timing: bool = False,
              no_buses: bool = True):
        """Write VCD file from measurement data

        Note: no_buses defaults to True because PulseView/sigrok doesn't support
        multi-bit VCD signals well - it expects individual boolean channels.
        """
        self.no_buses = no_buses
        meas = reader.ref_measurement if use_ref else reader.new_measurement
        if meas is None:
            raise ValueError("No measurement data available")

        # Find active analyzer and determine acquisition type
        active_analyzer = None
        analyzer_idx = -1
        for idx, analyzer in enumerate(meas.analyzers):
            if analyzer.mode != 4:  # Not off
                active_analyzer = analyzer
                analyzer_idx = idx
                break

        if active_analyzer is None:
            raise ValueError("No active analyzer found in measurement")

        # Build channel to label mapping
        self._build_channel_label_map(reader, analyzer_idx)

        # Determine if we have timing or state data
        # Mode 0: Timing only
        # Mode 1: State only
        # Mode 2: Timing+Glitch (timing is primary)
        # Mode 3: State+Timing (state is primary, timing is supplementary)
        # Mode 4: Off
        if force_timing and active_analyzer.mode == 3:
            is_timing_primary = True
            is_state_primary = False
        else:
            is_timing_primary = active_analyzer.mode in (0, 2)  # Timing, Timing+Glitch
            is_state_primary = active_analyzer.mode in (1, 3)   # State, State+Timing

        # Get connected pods for this analyzer
        connected_pods = []
        for pod_idx, pod in enumerate(meas.pods):
            if pod.connection == analyzer_idx:
                connected_pods.append((pod_idx, pod))

        if not connected_pods:
            raise ValueError(f"No pods connected to analyzer {analyzer_idx + 1}")

        with open(self.output_path, 'w') as f:
            # Write VCD header
            f.write("$version PM3585 to VCD Converter $end\n")
            f.write(f"$timescale {self.timescale_ns}ns $end\n")

            # Calculate date from measurement time
            try:
                meas_date = EPOCH_1976 + datetime.timedelta(seconds=meas.meas_time * 0.5)
                f.write(f"$date {meas_date.strftime('%Y-%m-%d %H:%M:%S')} $end\n")
            except Exception:
                f.write("$date Unknown $end\n")

            f.write("$scope module logic_analyzer $end\n")

            # Build list of active channels and determine which are part of buses
            active_channels = []  # List of (pod_idx, ch, global_ch)
            for pod_idx, pod in connected_pods:
                if is_timing_primary:
                    channel_alloc = pod.timing_channel_alloc
                else:
                    channel_alloc = pod.state_channel_alloc

                for ch in range(16):
                    if channel_alloc & (1 << ch):
                        global_ch = pod_idx * 16 + ch
                        if not self.all_channels and global_ch not in self.channel_to_label:
                            continue
                        active_channels.append((pod_idx, ch, global_ch))

            # First, define buses (multi-bit signals) unless --no-buses is set
            if not self.no_buses:
                defined_buses = set()
                for pod_idx, ch, global_ch in active_channels:
                    if global_ch in self.channel_to_bus:
                        bus_name = self.channel_to_bus[global_ch]
                        if bus_name not in defined_buses:
                            defined_buses.add(bus_name)
                            bus_channels = self.bus_definitions[bus_name]
                            width = len(bus_channels)
                            vcd_id = self._next_id()
                            # Build list of (pod_idx, ch) for each bit
                            bit_keys = []
                            for bit_ch in bus_channels:
                                bit_pod = bit_ch // 16
                                bit_ch_in_pod = bit_ch % 16
                                bit_keys.append((bit_pod, bit_ch_in_pod))
                            self.buses[bus_name] = (vcd_id, width, bit_keys)
                            # Sanitize name for VCD
                            vcd_name = bus_name.replace('[', '_').replace(']', '_').replace('.', '_').replace(' ', '_')
                            f.write(f"$var wire {width} {vcd_id} {vcd_name} $end\n")

            # Then, define single-bit signals (not part of any bus, or all if --no-buses)
            for pod_idx, ch, global_ch in active_channels:
                if self.no_buses or global_ch not in self.channel_to_bus:
                    signal_name = self._get_signal_name(pod_idx, ch)
                    signal_key = (pod_idx, ch)
                    vcd_id = self._next_id()
                    self.signals[signal_key] = vcd_id
                    # Sanitize name for VCD
                    vcd_name = signal_name.replace('[', '_').replace(']', '_').replace(' ', '_')
                    f.write(f"$var wire 1 {vcd_id} {vcd_name} $end\n")

            f.write("$upscope $end\n")
            f.write("$enddefinitions $end\n")

            # Write initial values
            f.write("#0\n")
            f.write("$dumpvars\n")
            # Buses get 'bxxx...' format
            for bus_name, (vcd_id, width, _) in self.buses.items():
                f.write(f"b{'x' * width} {vcd_id}\n")
            # Single signals get 'x' format
            for signal_key, vcd_id in self.signals.items():
                f.write(f"x{vcd_id}\n")
            f.write("$end\n")

            # Write data based on acquisition mode
            if is_timing_primary:
                self._write_timing_data(f, meas, analyzer_idx, connected_pods, active_analyzer)
            else:
                self._write_state_data(f, meas, analyzer_idx, connected_pods, active_analyzer)

        print(f"Written VCD file: {self.output_path}")
        print(f"  Buses: {len(self.buses)}")
        print(f"  Signals: {len(self.signals)}")
        print(f"  Timescale: {self.timescale_ns}ns")

    def _write_timing_data(self, f, meas: Measurement, analyzer_idx: int,
                           connected_pods: list, analyzer: AnalyzerAcquisition):
        """Write timing mode data to VCD.

        The PM3585 timing data stores up to 4 samples per status entry.
        Data is stored sequentially - approximately 4 data entries per status entry
        for each active pod.
        """
        status = analyzer.timing_status
        if not status.timestamps:
            return

        # Initialize data indices for each pod (indices into pod.timing_data)
        pod_data_idx = {pod_idx: 0 for pod_idx, _ in connected_pods}

        # Track last known state for each signal/bus (for change detection in VCD)
        last_values = {}      # (pod_idx, ch) -> bit value
        last_bus_values = {}  # bus_name -> binary string

        def write_signal_changes(vcd_time: int, pod_values: dict):
            """Write only changed values to VCD, handling both single signals and buses"""
            nonlocal last_values, last_bus_values
            changes = []       # List of (vcd_id, value_str) for single bits
            bus_changes = []   # List of (vcd_id, binary_str) for buses

            # Check single-bit signals for changes
            for (pod_idx, ch), value in pod_values.items():
                signal_key = (pod_idx, ch)
                if signal_key in self.signals:
                    # This is a single-bit signal, check for change
                    old_value = last_values.get(signal_key)
                    if old_value is None or old_value != value:
                        changes.append((self.signals[signal_key], str(value)))

            # Check buses for changes - build binary string from constituent bits
            for bus_name, (vcd_id, width, bit_keys) in self.buses.items():
                # Build binary string (MSB first for VCD)
                bits = []
                for bit_key in reversed(bit_keys):  # Reverse so MSB is first
                    bit_val = pod_values.get(bit_key, last_values.get(bit_key, 0))
                    bits.append(str(bit_val))
                binary_str = ''.join(bits)

                # Check if bus value changed
                if bus_name not in last_bus_values or last_bus_values[bus_name] != binary_str:
                    last_bus_values[bus_name] = binary_str
                    bus_changes.append((vcd_id, binary_str))

            # Update last_values after checking for changes
            for (pod_idx, ch), value in pod_values.items():
                last_values[(pod_idx, ch)] = value

            # Write all changes at this timestamp
            if changes or bus_changes:
                f.write(f"#{vcd_time}\n")
                for vcd_id, binary_str in bus_changes:
                    f.write(f"b{binary_str} {vcd_id}\n")
                for vcd_id, value in changes:
                    f.write(f"{value}{vcd_id}\n")

        def get_pod_data(pod_idx: int, pod: PodAcquisition) -> dict:
            """Get current channel values for a pod"""
            values = {}
            data_idx = pod_data_idx[pod_idx]
            if data_idx < len(pod.timing_data):
                data = pod.timing_data[data_idx]
                for ch in range(16):
                    if pod.timing_channel_alloc & (1 << ch):
                        values[(pod_idx, ch)] = (data >> ch) & 1
            return values

        # Process each status code entry
        # Per Appendix C of MEAS_3_4.txt:
        # - TimeCode[E], StatusCode[E] and current data entries = sub-sample 0
        # - If pod has transition, increment data index
        # - TimeCode[E+1] - 3 = sub-sample 1
        # - If pod has transition, increment data index
        # - TimeCode[E+1] - 2 = sub-sample 2
        # - If pod has transition, increment data index
        # - TimeCode[E+1] - 1 = sub-sample 3
        # - If pod has transition, increment data index
        num_entries = len(status.status_codes)
        for entry_idx in range(num_entries):
            status_code = status.status_codes[entry_idx]
            timestamp = status.timestamps[entry_idx]

            # Decode status code (2 bytes, big-endian as read):
            # High byte (byte 0): bits 4-7 = trans_entries, bits 0-3 = seq/stop
            # Low byte (byte 1): bits 0-5 = trans_pods
            byte0 = (status_code >> 8) & 0xFF
            byte1 = status_code & 0xFF
            trans_entries = (byte0 >> 4) & 0x0F  # bits 4-7 of byte 0
            trans_pods = byte1 & 0x3F            # bits 0-5 of byte 1

            # Get next timestamp for calculating sub-sample times
            if entry_idx + 1 < num_entries:
                next_timestamp = status.timestamps[entry_idx + 1]
            else:
                next_timestamp = timestamp + 4

            # Process 4 sub-samples per status entry
            for sub_sample in range(4):
                # Calculate time for this sub-sample
                if sub_sample == 0:
                    sample_time = timestamp
                else:
                    # Sub-samples 1,2,3 are at next_timestamp - 3, -2, -1
                    sample_time = next_timestamp - (4 - sub_sample)

                # Convert to VCD time (make positive, relative to start)
                vcd_time = sample_time - status.timestamps[0]
                if vcd_time < 0:
                    vcd_time = 0

                # Collect current values from all pods
                all_values = {}
                for pod_idx, pod in connected_pods:
                    data_idx = pod_data_idx[pod_idx]
                    if data_idx < len(pod.timing_data):
                        data = pod.timing_data[data_idx]
                        for ch in range(16):
                            if pod.timing_channel_alloc & (1 << ch):
                                all_values[(pod_idx, ch)] = (data >> ch) & 1

                # Write changes to VCD
                write_signal_changes(vcd_time, all_values)

                # After reading each sub-sample, if this pod has a transition bit set,
                # advance its data index (per Appendix C pseudo-code)
                for pod_idx, pod in connected_pods:
                    if trans_pods & (1 << pod_idx):
                        if pod_data_idx[pod_idx] + 1 < len(pod.timing_data):
                            pod_data_idx[pod_idx] += 1

    def _write_state_data(self, f, meas: Measurement, analyzer_idx: int,
                          connected_pods: list, analyzer: AnalyzerAcquisition):
        """Write state mode data to VCD"""
        status = analyzer.state_status
        if not status.timestamps:
            return

        # Get the first timestamp to use as offset (make all times positive)
        first_timestamp = status.timestamps[0] if status.timestamps else 0

        # Track last values for change detection (VCD only needs changes)
        last_values = {}

        # For state mode, there's a 1:1 correspondence between entries
        for entry_idx, (status_code, timestamp) in enumerate(
                zip(status.status_codes, status.timestamps)):

            # Calculate VCD time (relative to first sample, always positive)
            vcd_time = timestamp - first_timestamp

            # Collect all channel values for this entry
            changes = []
            for pod_idx, pod in connected_pods:
                if entry_idx < len(pod.state_data):
                    data = pod.state_data[entry_idx]

                    # Check each channel value
                    for ch in range(16):
                        if pod.state_channel_alloc & (1 << ch):
                            signal_key = (pod_idx, ch)
                            if signal_key in self.signals:
                                value = (data >> ch) & 1
                                # Only record if changed from last value
                                if signal_key not in last_values or last_values[signal_key] != value:
                                    last_values[signal_key] = value
                                    changes.append((self.signals[signal_key], value))

            # Write timestamp and changes if any
            if changes:
                f.write(f"#{vcd_time}\n")
                for vcd_id, value in changes:
                    f.write(f"{value}{vcd_id}\n")


class CSVWriter:
    """Writer for CSV files"""

    def __init__(self, output_path: str, sample_rate_hz: int = 200_000_000):
        self.output_path = output_path
        self.sample_rate_hz = sample_rate_hz
        self.channel_to_label: dict = {}
        self.all_channels = True

    def _build_channel_label_map(self, reader: PM3585Reader, analyzer_idx: int):
        """Build mapping from channel numbers to label names"""
        self.channel_to_label = {}

        if reader.settings is None or analyzer_idx >= len(reader.settings.analyzers):
            return

        analyzer_settings = reader.settings.analyzers[analyzer_idx]

        for label in analyzer_settings.labels:
            if not label.name or not label.channels:
                continue

            if len(label.channels) == 1:
                self.channel_to_label[label.channels[0]] = (label.name, None)
            else:
                for bit_idx, channel in enumerate(label.channels):
                    self.channel_to_label[channel] = (label.name, bit_idx)

    def _get_signal_name(self, pod_idx: int, channel_in_pod: int) -> str:
        """Get signal name for a channel"""
        global_channel = (pod_idx * 16) + channel_in_pod

        if global_channel in self.channel_to_label:
            label_name, bit_idx = self.channel_to_label[global_channel]
            if bit_idx is not None:
                return f"{label_name}[{bit_idx}]"
            else:
                return label_name

        return f"pod{pod_idx + 1}_ch{channel_in_pod}"

    def write(self, reader: PM3585Reader, use_ref: bool = False, force_timing: bool = False):
        """Write CSV file from measurement data"""
        meas = reader.ref_measurement if use_ref else reader.new_measurement
        if meas is None:
            raise ValueError("No measurement data available")

        # Find active analyzer
        active_analyzer = None
        analyzer_idx = -1
        for idx, analyzer in enumerate(meas.analyzers):
            if analyzer.mode != 4:
                active_analyzer = analyzer
                analyzer_idx = idx
                break

        if active_analyzer is None:
            raise ValueError("No active analyzer found in measurement")

        self._build_channel_label_map(reader, analyzer_idx)

        if force_timing and active_analyzer.mode == 3:
            is_state_primary = False
        else:
            is_state_primary = active_analyzer.mode in (1, 3)

        # Get connected pods
        connected_pods = []
        for pod_idx, pod in enumerate(meas.pods):
            if pod.connection == analyzer_idx:
                connected_pods.append((pod_idx, pod))

        if not connected_pods:
            raise ValueError(f"No pods connected to analyzer {analyzer_idx + 1}")

        # Build list of signals
        signals = []  # List of (pod_idx, ch, name)
        for pod_idx, pod in connected_pods:
            channel_alloc = pod.state_channel_alloc if is_state_primary else pod.timing_channel_alloc
            for ch in range(16):
                if channel_alloc & (1 << ch):
                    global_ch = pod_idx * 16 + ch
                    if not self.all_channels and global_ch not in self.channel_to_label:
                        continue
                    name = self._get_signal_name(pod_idx, ch)
                    signals.append((pod_idx, ch, name))

        # Write CSV
        with open(self.output_path, 'w') as f:
            # Header: Time [s], Channel 0, Channel 1, ...
            header = ["Time [s]"] + [name for _, _, name in signals]
            f.write(",".join(header) + "\n")

            # Get timestamps and write data rows
            if is_state_primary:
                self._write_state_data(f, meas, connected_pods, signals, active_analyzer)
            else:
                self._write_timing_data(f, meas, connected_pods, signals, active_analyzer)

        print(f"Written CSV file: {self.output_path}")
        print(f"  Signals: {len(signals)}")
        print(f"  Sample rate: {self.sample_rate_hz / 1_000_000:.0f} MHz")

    def _write_state_data(self, f, meas: Measurement, connected_pods: list,
                          signals: list, analyzer: AnalyzerAcquisition):
        """Write state data to CSV"""
        status = analyzer.state_status
        if not status.timestamps:
            return

        first_timestamp = status.timestamps[0] if status.timestamps else 0
        time_unit_sec = 5e-9  # 5ns per unit

        # Build signal lookup
        sig_lookup = {}
        for sig_idx, (pod_idx, ch, _) in enumerate(signals):
            sig_lookup[(pod_idx, ch)] = sig_idx

        for entry_idx, timestamp in enumerate(status.timestamps):
            time_sec = (timestamp - first_timestamp) * time_unit_sec

            # Collect values for all signals
            values = [0] * len(signals)
            for pod_idx, pod in connected_pods:
                if entry_idx < len(pod.state_data):
                    data = pod.state_data[entry_idx]
                    for ch in range(16):
                        if pod.state_channel_alloc & (1 << ch):
                            key = (pod_idx, ch)
                            if key in sig_lookup:
                                values[sig_lookup[key]] = (data >> ch) & 1

            # Write row
            row = [f"{time_sec:.12f}"] + [str(v) for v in values]
            f.write(",".join(row) + "\n")

    def _write_timing_data(self, f, meas: Measurement, connected_pods: list,
                           signals: list, analyzer: AnalyzerAcquisition):
        """Write timing data to CSV (up to 4 samples per status entry)"""
        status = analyzer.timing_status
        if not status.timestamps:
            return

        first_timestamp = status.timestamps[0] if status.timestamps else 0
        time_unit_sec = 5e-9

        sig_lookup = {}
        for sig_idx, (pod_idx, ch, _) in enumerate(signals):
            sig_lookup[(pod_idx, ch)] = sig_idx

        pod_data_idx = {pod_idx: 0 for pod_idx, _ in connected_pods}
        last_values = [0] * len(signals)
        last_time = None

        num_entries = len(status.status_codes)
        for entry_idx in range(num_entries):
            timestamp = status.timestamps[entry_idx]

            if entry_idx + 1 < num_entries:
                next_timestamp = status.timestamps[entry_idx + 1]
            else:
                next_timestamp = timestamp + 4

            for sub_sample in range(4):
                if sub_sample == 0:
                    sample_time = timestamp
                else:
                    sample_time = next_timestamp - (4 - sub_sample)

                time_sec = (sample_time - first_timestamp) * time_unit_sec
                if time_sec < 0:
                    time_sec = 0

                # Collect current values
                values = last_values.copy()
                for pod_idx, pod in connected_pods:
                    data_idx = pod_data_idx[pod_idx]
                    if data_idx < len(pod.timing_data):
                        data = pod.timing_data[data_idx]
                        for ch in range(16):
                            if pod.timing_channel_alloc & (1 << ch):
                                key = (pod_idx, ch)
                                if key in sig_lookup:
                                    values[sig_lookup[key]] = (data >> ch) & 1

                # Only write if values changed or first row
                if values != last_values or last_time is None:
                    row = [f"{time_sec:.12f}"] + [str(v) for v in values]
                    f.write(",".join(row) + "\n")
                    last_values = values.copy()
                    last_time = time_sec

                # Advance data index for each pod
                for pod_idx, pod in connected_pods:
                    if pod_data_idx[pod_idx] + 1 < len(pod.timing_data):
                        pod_data_idx[pod_idx] += 1


def main():
    parser = argparse.ArgumentParser(
        description="Convert PM3585 logic analyzer files to VCD or CSV format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s measurement.mea                    # Convert to measurement.vcd
  %(prog)s measurement.mea -o output.vcd      # Specify output file
  %(prog)s measurement.mea --format csv       # Convert to CSV format
  %(prog)s measurement.mea --ref              # Convert REF measurement instead of NEW
  %(prog)s measurement.mea --info             # Show file information only
        """
    )

    parser.add_argument("input", help="Input PM3585 measurement file")
    parser.add_argument("-o", "--output", help="Output file (default: input.vcd or input.csv)")
    parser.add_argument("-f", "--format", choices=["vcd", "csv"], default="vcd",
                        help="Output format: vcd (sigrok/PulseView), csv (generic)")
    parser.add_argument("--ref", action="store_true",
                        help="Convert REF measurement instead of NEW")
    parser.add_argument("--timing", action="store_true",
                        help="Use timing data instead of state data (for mode 3 captures)")
    parser.add_argument("--buses", action="store_true",
                        help="Group multi-bit labels into VCD buses (experimental, may not work in PulseView)")
    parser.add_argument("--info", action="store_true",
                        help="Show file information and exit")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose output")

    args = parser.parse_args()

    # Read input file
    try:
        reader = PM3585Reader(args.input).read()
    except FileNotFoundError:
        print(f"Error: File not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Show file info
    print(f"File: {args.input}")
    print(f"  Version: {reader.header.version}")
    print(f"  Frequency: {reader.header.frequency_mhz} MHz")
    print(f"  Pods: {reader.header.num_pods}")
    print(f"  Analyzers: {reader.header.num_analyzers}")
    print(f"  NEW measurement: {'Present' if reader.new_measurement else 'Empty'}")
    print(f"  REF measurement: {'Present' if reader.ref_measurement else 'Empty'}")

    if reader.new_measurement and args.verbose:
        meas = reader.new_measurement
        print("\nNEW Measurement details:")
        for idx, analyzer in enumerate(meas.analyzers):
            mode_names = {0: "Timing", 1: "State", 2: "Timing+Glitch",
                          3: "State+Timing", 4: "Off"}
            print(f"  Analyzer {idx + 1}: {mode_names.get(analyzer.mode, 'Unknown')}")
            if analyzer.mode != 4:
                print(f"    Timing samples: {analyzer.timing_status.num_samples}")
                print(f"    State samples: {analyzer.state_status.num_samples}")

        for idx, pod in enumerate(meas.pods):
            conn = {0: "Analyzer 1", 1: "Analyzer 2", 2: "Not connected"}
            print(f"  Pod {idx + 1}: {conn.get(pod.connection, 'Unknown')}")
            if pod.connection != 2:
                print(f"    Timing entries: {len(pod.timing_data)}")
                print(f"    State entries: {len(pod.state_data)}")
                print(f"    Glitch entries: {len(pod.glitch_data)}")

    # Show labels if available
    if reader.settings and args.verbose:
        print("\nLabels defined:")
        for idx, analyzer in enumerate(reader.settings.analyzers):
            if analyzer.labels:
                print(f"  Analyzer {idx + 1} ({analyzer.name or 'unnamed'}):")
                for label in analyzer.labels:
                    channels_str = ', '.join(str(ch) for ch in label.channels[:5])
                    if len(label.channels) > 5:
                        channels_str += f"... ({len(label.channels)} total)"
                    print(f"    {label.name}: channels [{channels_str}]")

    if args.info:
        sys.exit(0)

    # Determine which measurement to convert
    use_ref = args.ref
    meas = reader.ref_measurement if use_ref else reader.new_measurement
    if meas is None:
        meas_type = "REF" if use_ref else "NEW"
        print(f"Error: {meas_type} measurement is empty", file=sys.stderr)
        sys.exit(1)

    # Determine output file
    if args.output:
        output_path = args.output
    else:
        suffixes = {'vcd': '.vcd', 'csv': '.csv'}
        output_path = Path(args.input).with_suffix(suffixes[args.format])

    # Write output file
    try:
        sample_rate = reader.header.frequency_mhz * 1_000_000
        force_timing = args.timing
        if args.format == 'csv':
            writer = CSVWriter(str(output_path), sample_rate_hz=sample_rate)
            writer.write(reader, use_ref=use_ref, force_timing=force_timing)
            print(f"\nConversion complete.")
        else:
            writer = VCDWriter(str(output_path))
            use_buses = getattr(args, 'buses', False)
            writer.write(reader, use_ref=use_ref, force_timing=force_timing,
                        no_buses=not use_buses)
            print(f"\nConversion complete. Open with: pulseview {output_path}")
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    import datetime
    main()
