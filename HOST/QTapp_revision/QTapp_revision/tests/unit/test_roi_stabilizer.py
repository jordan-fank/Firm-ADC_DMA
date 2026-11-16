import pytest

from core.calib_controller import _RoiStabilizer


@pytest.mark.unit
def test_roi_stabilizer_ignores_small_jitter():
    stabilizer = _RoiStabilizer(change_threshold=8)

    assert stabilizer.update((100, 120, 300, 220)) == (100, 120, 300, 220)
    assert stabilizer.update((103, 116, 304, 223)) == (100, 120, 300, 220)


@pytest.mark.unit
def test_roi_stabilizer_accepts_large_motion():
    stabilizer = _RoiStabilizer(change_threshold=8)

    assert stabilizer.update((100, 120, 300, 220)) == (100, 120, 300, 220)
    assert stabilizer.update((125, 147, 300, 220)) == (125, 147, 300, 220)


@pytest.mark.unit
def test_roi_stabilizer_keeps_last_roi_when_detection_is_lost_briefly():
    stabilizer = _RoiStabilizer(change_threshold=8, lost_hold_frames=2)

    assert stabilizer.update((100, 120, 300, 220)) == (100, 120, 300, 220)
    assert stabilizer.update(None) == (100, 120, 300, 220)
    assert stabilizer.update(None) == (100, 120, 300, 220)
    assert stabilizer.update(None) is None
