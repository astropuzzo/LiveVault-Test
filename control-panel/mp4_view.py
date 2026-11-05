"""Seekable flat MP4 view over existing fMP4 media, without copying video data."""
from array import array
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
import io
import struct
import sys

MAX_INDEX = 4 * 1024**2


def box(kind, payload):
    return struct.pack('>I4s', len(payload) + 8, kind) + payload


def children(data):
    at = 0
    while at < len(data):
        if len(data) - at < 8:
            raise ValueError('Truncated MP4 box')
        size, kind = struct.unpack_from('>I4s', data, at)
        head = 8
        if size == 1:
            size = struct.unpack_from('>Q', data, at + 8)[0]
            head = 16
        if size < head or at + size > len(data):
            raise ValueError('Invalid MP4 box size')
        yield kind, data[at + head:at + size]
        at += size


def child(data, name):
    return next(payload for kind, payload in children(data) if kind == name)


def top_boxes(path):
    with path.open('rb') as source:
        end = path.stat().st_size
        while source.tell() < end:
            start = source.tell()
            head = source.read(8)
            if len(head) != 8:
                raise ValueError('Truncated MP4 file')
            size, kind = struct.unpack('>I4s', head)
            header_size = 8
            if size == 1:
                size = struct.unpack('>Q', source.read(8))[0]
                header_size = 16
            if size < header_size or start + size > end:
                raise ValueError('Invalid MP4 file box')
            payload = source.read(size - header_size) if kind in {b'moov', b'moof'} else b''
            yield start, kind, payload
            source.seek(start + size)


def be(values, code='I'):
    result = array(code, values)
    if sys.byteorder == 'little':
        result.byteswap()
    return result.tobytes()


def table(kind, values, count):
    return box(kind, b'\0' * 4 + struct.pack('>I', count) + be(values))


def timed(payload, duration, scale=None, track=False):
    result = bytearray(payload)
    version = payload[0]
    if track:
        offset, fmt = (28, '>Q') if version else (20, '>I')
    else:
        scale_offset, offset, fmt = (20, 24, '>Q') if version else (12, 16, '>I')
        if scale is not None:
            struct.pack_into('>I', result, scale_offset, scale)
    struct.pack_into(fmt, result, offset, duration)
    return bytes(result)


@dataclass(frozen=True)
class Chunk:
    path: Path
    offset: int
    length: int
    relative: int


class Movie:
    def __init__(self, header, chunks, media_size):
        self.header = header
        self.chunks = chunks
        self.positions = [len(header) + chunk.relative for chunk in chunks]
        self.size = len(header) + media_size
        self.read_allowed = None

    def open(self, mode='rb'):
        if mode != 'rb':
            raise ValueError('Read-only movie')
        return Reader(self)


class Reader:
    def __init__(self, movie):
        self.movie, self.position, self.source, self.source_path = movie, 0, None, None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self.source:
            self.source.close()
        self.source = self.source_path = None

    def seek(self, offset):
        if not 0 <= offset <= self.movie.size:
            raise ValueError('Invalid movie offset')
        self.position = offset

    def read(self, size):
        if self.movie.read_allowed and not self.movie.read_allowed():
            raise OSError('Storage handoff in progress')
        remaining = min(size, self.movie.size - self.position)
        result = bytearray()
        if self.position < len(self.movie.header):
            length = min(remaining, len(self.movie.header) - self.position)
            result += self.movie.header[self.position:self.position + length]
            self.position += length
            remaining -= length
        while remaining:
            index = bisect_right(self.movie.positions, self.position) - 1
            chunk = self.movie.chunks[index]
            offset = self.position - self.movie.positions[index]
            length = min(remaining, chunk.length - offset)
            if self.source_path != chunk.path:
                self.close()
                self.source = chunk.path.open('rb')
                self.source_path = chunk.path
            self.source.seek(chunk.offset + offset)
            data = self.source.read(length)
            if len(data) != length:
                raise OSError('Archive media no longer available')
            result += data
            self.position += length
            remaining -= length
        return bytes(result)


def build(paths, selections=None):
    """Construct ftyp/moov/mdat headers and byte mappings; never read mdat payloads."""
    paths = list(paths)
    if not paths:
        raise ValueError('Empty movie')
    selections = [None] * len(paths) if selections is None else list(selections)
    if len(selections) != len(paths):
        raise ValueError('Sample selection does not match media')
    sizes, sync, runs, chunks, sample_map = array('I'), array('I'), array('i'), [], array('I')
    total, count, minimum, original, codec = 0, 0, 0, None, None
    scale, step = None, None
    for path, selected in zip(paths,selections):
        selected = None if selected is None else frozenset(selected)
        if selected is not None and any(type(index) is not int or index < 0 for index in selected):
            raise ValueError('Invalid sample selection')
        source_index = 0
        if path.is_symlink():
            raise ValueError('Symlink media is not allowed')
        for start, kind, payload in top_boxes(path):
            if kind == b'moov':
                track = child(payload, b'trak')
                media = child(track, b'mdia')
                mdhd = child(media, b'mdhd')
                source_scale = struct.unpack_from('>I', mdhd, 20 if mdhd[0] else 12)[0]
                stsd = child(child(child(media, b'minf'), b'stbl'), b'stsd')
                # Ignore container bitrate hints; compare the actual AVC decoder configuration.
                avcc_at = stsd.find(b'avcC')
                if avcc_at < 4:
                    raise ValueError('AVC decoder configuration missing')
                avcc_size = struct.unpack_from('>I', stsd, avcc_at - 4)[0]
                source_codec = stsd[avcc_at - 4:avcc_at - 4 + avcc_size]
                if original is None:
                    original, codec, scale = payload, source_codec, source_scale
                elif source_codec != codec or source_scale != scale:
                    raise ValueError('Archive decoder configuration changed')
            if kind != b'moof':
                continue
            traf = child(payload, b'traf')
            tfhd = child(traf, b'tfhd')
            flags, at = int.from_bytes(tfhd[1:4], 'big'), 8
            base = start
            if flags & 1:
                base = struct.unpack_from('>Q', tfhd, at)[0]; at += 8
            if flags & 2:
                at += 4
            duration = sample_size = sample_flags = 0
            for bit, name in [(8,'duration'), (16,'size'), (32,'flags')]:
                if flags & bit:
                    value = struct.unpack_from('>I', tfhd, at)[0]; at += 4
                    if name == 'duration': duration = value
                    elif name == 'size': sample_size = value
                    else: sample_flags = value
            for entry_kind, run in children(traf):
                if entry_kind != b'trun':
                    continue
                run_flags = int.from_bytes(run[1:4], 'big')
                sample_count = struct.unpack_from('>I', run, 4)[0]
                at = 8
                if not run_flags & 1:
                    raise ValueError('MP4 run data offset missing')
                source_offset = base + struct.unpack_from('>i', run, at)[0]; at += 4
                first_flags = None
                if run_flags & 4:
                    first_flags = struct.unpack_from('>I', run, at)[0]; at += 4
                length, kept = 0, []
                for index in range(sample_count):
                    sample_duration, size, current_flags, composition = duration, sample_size, sample_flags, 0
                    if run_flags & 0x100:
                        sample_duration = struct.unpack_from('>I', run, at)[0]; at += 4
                    if run_flags & 0x200:
                        size = struct.unpack_from('>I', run, at)[0]; at += 4
                    if run_flags & 0x400:
                        current_flags = struct.unpack_from('>I', run, at)[0]; at += 4
                    elif index == 0 and first_flags is not None:
                        current_flags = first_flags
                    if run_flags & 0x800:
                        composition = struct.unpack_from('>i' if run[0] else '>I', run, at)[0]; at += 4
                    if not sample_duration or not size:
                        raise ValueError('Invalid MP4 sample')
                    if step is None:
                        step = sample_duration
                    if sample_duration != step:
                        raise ValueError('Archive sample rate changed')
                    offset = source_offset + length
                    length += size
                    include = selected is None or source_index in selected
                    source_index += 1
                    if not include:
                        continue
                    if selected is not None and (current_flags & 0x10000 or composition):
                        raise ValueError('Sample selection requires independent frames')
                    if kept and kept[-1][0] + kept[-1][1] == offset:
                        kept[-1][1] += size; kept[-1][2] += 1
                    else:
                        kept.append([offset,size,1])
                    sizes.append(size); count += 1
                    if not current_flags & 0x10000:
                        sync.append(count)
                    minimum = min(minimum, composition)
                    if runs and runs[-1] == composition:
                        runs[-2] += 1
                    else:
                        runs.extend((1, composition))
                if source_offset < 0 or source_offset + length > path.stat().st_size:
                    raise ValueError('Invalid MP4 media bounds')
                for offset, size, kept_count in kept:
                    chunks.append(Chunk(path, offset, size, total)); total += size
                    if not sample_map or sample_map[-2] != kept_count:
                        sample_map.extend((len(chunks), kept_count, 1))
                if sizes.itemsize * len(sizes) + runs.itemsize * len(runs) + 20 * len(chunks) > MAX_INDEX:
                    raise ValueError('Preview index too large; choose a shorter window')
        if selected is not None and any(index >= source_index for index in selected):
            raise ValueError('Selected sample missing from media')
    if not count or not original or not scale or not step:
        raise ValueError('No video samples')
    lead, duration = -minimum, count * step
    ctts = array('I', (value + lead if index % 2 else value for index, value in enumerate(runs)))
    track = child(original, b'trak')
    media = child(track, b'mdia')
    media_info = child(media, b'minf')
    old_stbl = child(media_info, b'stbl')

    def movie_header(offsets):
        stbl = (box(b'stsd', child(old_stbl, b'stsd')) + table(b'stts', (count,step), 1)
                + table(b'ctts', ctts, len(ctts)//2) + table(b'stsc', sample_map, len(sample_map)//3)
                + box(b'stsz', b'\0'*4 + struct.pack('>II',0,count) + be(sizes))
                + box(b'co64', b'\0'*4 + struct.pack('>I',len(offsets)) + be(offsets,'Q'))
                + table(b'stss', sync, len(sync)))
        minf = b''.join(box(k, stbl if k == b'stbl' else p) for k,p in children(media_info))
        mdia = b''.join(box(k, timed(p,duration+lead,scale) if k == b'mdhd' else minf if k == b'minf' else p) for k,p in children(media))
        edit = box(b'edts', box(b'elst', b'\0'*4 + struct.pack('>IIiI',1,duration,lead,0x10000)))
        trak = b''
        for kind, payload in children(track):
            if kind == b'edts':
                continue
            if kind == b'tkhd':
                trak += box(kind, timed(payload,duration,track=True)) + edit
            else:
                trak += box(kind, mdia if kind == b'mdia' else payload)
        moov = b''
        for kind, payload in children(original):
            if kind == b'mvex':
                continue
            moov += box(kind, timed(payload,duration,scale) if kind == b'mvhd' else trak if kind == b'trak' else payload)
        return box(b'moov', moov)

    ftyp = box(b'ftyp', b'isom' + struct.pack('>I',512) + b'isomiso2avc1mp41')
    moov = movie_header([0] * len(chunks))
    base = len(ftyp) + len(moov) + 16
    moov = movie_header([base + chunk.relative for chunk in chunks])
    header = ftyp + moov + struct.pack('>I4sQ',1,b'mdat',total+16)
    if len(header) > MAX_INDEX:
        raise ValueError('Preview index too large; choose a shorter window')
    return Movie(header, chunks, total)
