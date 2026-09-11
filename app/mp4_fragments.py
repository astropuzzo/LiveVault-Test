"""Conservative repair of empty leading samples in provider fMP4 fragments."""
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
    """Remove only leading zero-byte entries with explicit duration/size.

    Advance tfdt by their duration, retaining every media byte and all offsets.
    Vacated table bytes become a free box inside traf. Unknown layouts remain
    unchanged, so normal integrity checks still report unsupported corruption.
    """
    result = bytearray(data)
    for pos, size, kind in _boxes(data, 8, len(data)):
        if kind != b"traf":
            continue
        children = list(_boxes(data, pos + 8, pos + size))
        runs = [(p, s) for p, s, k in children if k == b"trun"]
        clocks = [(p, s) for p, s, k in children if k == b"tfdt"]
        if len(runs) != 1 or len(clocks) != 1:
            continue
        run, length = runs[0]
        clock, clock_size = clocks[0]
        if length < 20 or data[run + 8:run + 12] != b"\x00\x00\x03\x01":
            continue
        count = struct.unpack_from(">I", data, run + 12)[0]
        if count == 0 or length != 20 + count * 8:
            continue
        entries = [struct.unpack_from(">II", data, run + 20 + i * 8) for i in range(count)]
        leading = 0
        duration = 0
        for ticks, byte_count in entries:
            if byte_count:
                break
            leading += 1
            duration += ticks
        if not leading or leading == count or any(n == 0 for _, n in entries[leading:]):
            continue
        version = data[clock + 8] if clock_size >= 12 else -1
        width = 8 if version == 1 else 4 if version == 0 else 0
        if not width or clock_size != 12 + width:
            continue
        timestamp = int.from_bytes(data[clock + 12:clock + 12 + width], "big") + duration
        if timestamp >= 1 << (8 * width):
            continue
        result[clock + 12:clock + 12 + width] = timestamp.to_bytes(width, "big")
        removed = leading * 8
        new_length = length - removed
        struct.pack_into(">I", result, run, new_length)
        struct.pack_into(">I", result, run + 12, count - leading)
        result[run + 20:run + new_length] = data[run + 20 + removed:run + length]
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
