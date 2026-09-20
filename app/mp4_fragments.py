"""Conservative normalization of zero-byte samples in provider fMP4 fragments."""
from __future__ import annotations

import shutil
import struct
from pathlib import Path


def _boxes(data: bytes | bytearray, start: int, end: int):
    while start < end:
        if end - start < 8:
            raise ValueError("Truncated MP4 box")
        size, kind = struct.unpack_from(">I4s", data, start)
        if size < 8 or start + size > end:
            raise ValueError("Unsupported or truncated MP4 box")
        yield start, size, kind
        start += size


def repair_moof(data: bytes) -> bytes:
    """Remove zero-byte entries with explicit duration/size, preserving time.

    Leading empty duration advances tfdt; later empty duration extends the last
    real sample. Every retained sample keeps its DTS, flags and payload offset.
    Vacated table bytes become a free box. Unknown layouts remain unchanged.
    """
    result = bytearray(data)
    for pos, size, kind in _boxes(data, 8, len(data)):
        if kind != b"traf":
            continue
        children = list(_boxes(data, pos + 8, pos + size))
        # Auxiliary sample tables (e.g. encryption/grouping) would also need
        # reindexing. Never silently leave them referring to removed samples.
        if any(k not in (b"tfhd", b"tfdt", b"trun", b"free") for _, _, k in children):
            continue
        runs = [(p, s) for p, s, k in children if k == b"trun"]
        clocks = [(p, s) for p, s, k in children if k == b"tfdt"]
        if len(runs) != 1 or len(clocks) != 1:
            continue
        run, length = runs[0]
        clock, clock_size = clocks[0]
        if length < 20 or data[run + 8] != 0:
            continue
        flags = int.from_bytes(data[run + 9:run + 12], "big")
        # Observed provider layouts: duration/size plus data offset, optionally
        # first-sample flags OR per-sample flags. Do not guess other layouts.
        if flags not in (0x301, 0x305, 0x701):
            continue
        header = 24 if flags & 4 else 20
        stride = 12 if flags & 0x400 else 8
        count = struct.unpack_from(">I", data, run + 12)[0]
        if count == 0 or length != header + count * stride:
            continue
        entries = []
        leading_duration = 0
        leading_count = 0
        overflow = False
        for i in range(count):
            entry = bytearray(data[run + header + i * stride:run + header + (i + 1) * stride])
            ticks, byte_count = struct.unpack_from(">II", entry)
            if byte_count:
                entries.append(entry)
            elif entries:
                duration = struct.unpack_from(">I", entries[-1])[0] + ticks
                if duration >= 1 << 32:
                    overflow = True
                    break
                struct.pack_into(">I", entries[-1], 0, duration)
            else:
                leading_count += 1
                leading_duration += ticks
        if overflow or not entries or len(entries) == count:
            continue
        # If the first sample is removed, discard its flag override as well.
        # The next sample keeps inheriting tfhd/trex defaults, as it did before.
        drop_first_flags = bool(leading_count and flags & 4)
        new_header = header - (4 if drop_first_flags else 0)
        version = data[clock + 8] if clock_size >= 12 else -1
        width = 8 if version == 1 else 4 if version == 0 else 0
        if not width or clock_size != 12 + width:
            continue
        timestamp = int.from_bytes(data[clock + 12:clock + 12 + width], "big") + leading_duration
        if timestamp >= 1 << (8 * width):
            continue
        result[clock + 12:clock + 12 + width] = timestamp.to_bytes(width, "big")
        removed = (count - len(entries)) * stride + header - new_header
        new_length = length - removed
        struct.pack_into(">I", result, run, new_length)
        if drop_first_flags:
            result[run + 9:run + 12] = (flags & ~4).to_bytes(3, "big")
        struct.pack_into(">I", result, run + 12, len(entries))
        result[run + new_header:run + new_length] = b"".join(entries)
        result[run + new_length:run + length] = struct.pack(">I4s", removed, b"free") + bytes(removed - 8)
    return bytes(result)


def repair_fragment(data: bytes) -> bytes:
    result = bytearray(data)
    try:
        for pos, size, kind in _boxes(data, 0, len(data)):
            if kind == b"moof":
                result[pos:pos + size] = repair_moof(data[pos:pos + size])
    except ValueError:
        return data
    return bytes(result)


def repaired_copy(source: Path, target: Path, checkpoint=lambda: None) -> bool:
    """Create a separate corrected input only if a recognized defect exists."""
    patches = []
    end = source.stat().st_size
    with source.open("rb") as handle:
        while handle.tell() < end:
            checkpoint()
            pos = handle.tell()
            header = handle.read(8)
            if len(header) != 8:
                return False
            size, kind = struct.unpack(">I4s", header)
            if size < 8 or pos + size > end:
                return False
            if kind == b"moof":
                if size > 16 * 1024 * 1024:
                    return False
                original = header + handle.read(size - 8)
                try:
                    fixed = repair_moof(original)
                except ValueError:
                    return False
                if fixed != original:
                    patches.append((pos, fixed))
            else:
                handle.seek(pos + size)
    if not patches:
        return False
    with source.open("rb") as reader, target.open("xb") as writer:
        while block := reader.read(1024 * 1024):
            checkpoint()
            writer.write(block)
        for pos, fixed in patches:
            writer.seek(pos)
            writer.write(fixed)
    shutil.copystat(source, target)
    return True
