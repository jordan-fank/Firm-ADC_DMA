import numpy as np


def _speed_frame(rpm):
    frame = [
        0xA5,
        0x5A,
        0x52,
        0x04,
        rpm & 0xFF,
        (rpm >> 8) & 0xFF,
        (rpm >> 16) & 0xFF,
        (rpm >> 24) & 0xFF,
    ]
    frame.append(sum(frame) & 0xFF)
    return frame


def test_strip_speed_frames_removes_valid_frame():
    from utils.speed_frame_filter import strip_speed_frames

    clean, frames = strip_speed_frames([1, 2] + _speed_frame(600) + [3, 4])

    assert clean == [1, 2, 3, 4]
    assert len(frames) == 1
    assert frames[0]["rpm"] == 600


def test_strip_speed_frames_keeps_bad_checksum():
    from utils.speed_frame_filter import strip_speed_frames

    frame = _speed_frame(600)
    frame[-1] ^= 0x01
    clean, frames = strip_speed_frames([1] + frame + [2])

    assert clean == [1] + frame + [2]
    assert frames == []


def test_stream_parser_handles_split_speed_frame():
    from utils.speed_frame_filter import SpeedFrameStreamParser

    parser = SpeedFrameStreamParser()
    frame = bytes(_speed_frame(720))

    assert parser.feed(frame[:3]) == []
    assert parser.feed(frame[3:7]) == []
    frames = parser.feed(frame[7:])

    assert len(frames) == 1
    assert frames[0]["rpm"] == 720


def test_summarize_speed_frames():
    from utils.speed_frame_filter import strip_speed_frames, summarize_speed_frames

    _, frames = strip_speed_frames(_speed_frame(600) + _speed_frame(900))
    summary = summarize_speed_frames(frames)

    assert summary["frame_count"] == 2
    assert summary["latest_rpm"] == 900
    assert summary["avg_rpm"] == 750
    assert summary["min_rpm"] == 600
    assert summary["max_rpm"] == 900


def test_cylinder_reader_filters_speed_frames(temp_dir):
    from algorithms.cylinder_3d import read_data_from_file

    data_file = temp_dir / "mixed_data.txt"
    tokens = ["01", "02"] + [f"{b:02X}" for b in _speed_frame(600)] + ["03"]
    data_file.write_text(" ".join(tokens), encoding="utf-8")

    data = read_data_from_file(str(data_file))

    np.testing.assert_array_equal(data, np.array([1, 2, 3], dtype=np.float64))
