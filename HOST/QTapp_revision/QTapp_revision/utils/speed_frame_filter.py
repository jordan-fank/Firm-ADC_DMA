# -*- coding: utf-8 -*-
"""Helpers for removing MCU speed frames from saved ADC byte streams."""

SPEED_FRAME_HEADER = (0xA5, 0x5A, 0x52, 0x04)
SPEED_FRAME_HEADER_BYTES = bytes(SPEED_FRAME_HEADER)
SPEED_FRAME_SIZE = 9


def _is_byte(value):
    return 0 <= value <= 0xFF


def _decode_rpm(frame):
    return (
        frame[4]
        | (frame[5] << 8)
        | (frame[6] << 16)
        | (frame[7] << 24)
    )


def _is_valid_speed_frame(values, index):
    if index + SPEED_FRAME_SIZE > len(values):
        return False

    frame = values[index:index + SPEED_FRAME_SIZE]
    if any(not _is_byte(v) for v in frame):
        return False

    if tuple(frame[:4]) != SPEED_FRAME_HEADER:
        return False

    checksum = sum(frame[:SPEED_FRAME_SIZE - 1]) & 0xFF
    return checksum == frame[SPEED_FRAME_SIZE - 1]


def strip_speed_frames(values):
    """Return ADC values with valid A5 5A 52 speed frames removed."""
    source = [int(v) for v in values]
    filtered = []
    frames = []
    i = 0

    while i < len(source):
        if _is_valid_speed_frame(source, i):
            frame = tuple(source[i:i + SPEED_FRAME_SIZE])
            frames.append({
                "offset": i,
                "rpm": _decode_rpm(frame),
                "frame": frame,
            })
            i += SPEED_FRAME_SIZE
            continue

        filtered.append(source[i])
        i += 1

    return filtered, frames


def summarize_speed_frames(frames):
    """Return display-friendly RPM statistics for parsed speed frames."""
    rpms = [int(frame["rpm"]) for frame in frames]
    if not rpms:
        return {
            "frame_count": 0,
            "latest_rpm": None,
            "avg_rpm": None,
            "min_rpm": None,
            "max_rpm": None,
        }

    return {
        "frame_count": len(rpms),
        "latest_rpm": rpms[-1],
        "avg_rpm": sum(rpms) / len(rpms),
        "min_rpm": min(rpms),
        "max_rpm": max(rpms),
    }


class SpeedFrameStreamParser:
    """Incrementally parse speed frames from arbitrary serial byte chunks."""

    def __init__(self):
        self._buffer = bytearray()
        self._stream_offset = 0

    def reset(self):
        self._buffer.clear()
        self._stream_offset = 0

    def feed(self, data):
        if not data:
            return []

        self._buffer.extend(bytes(data))
        frames = []

        while True:
            header_index = self._buffer.find(SPEED_FRAME_HEADER_BYTES)
            if header_index < 0:
                keep = min(len(self._buffer), len(SPEED_FRAME_HEADER_BYTES) - 1)
                if keep:
                    dropped = len(self._buffer) - keep
                    del self._buffer[:dropped]
                    self._stream_offset += dropped
                else:
                    self._stream_offset += len(self._buffer)
                    self._buffer.clear()
                break

            if header_index > 0:
                del self._buffer[:header_index]
                self._stream_offset += header_index

            if len(self._buffer) < SPEED_FRAME_SIZE:
                break

            frame = tuple(self._buffer[:SPEED_FRAME_SIZE])
            checksum = sum(frame[:SPEED_FRAME_SIZE - 1]) & 0xFF
            if checksum == frame[SPEED_FRAME_SIZE - 1]:
                frames.append({
                    "offset": self._stream_offset,
                    "rpm": _decode_rpm(frame),
                    "frame": frame,
                })
                del self._buffer[:SPEED_FRAME_SIZE]
                self._stream_offset += SPEED_FRAME_SIZE
                continue

            del self._buffer[0]
            self._stream_offset += 1

        return frames
