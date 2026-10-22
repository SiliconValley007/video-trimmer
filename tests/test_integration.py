"""Real FFmpeg round trips. Skipped automatically when FFmpeg is unavailable."""

import subprocess

import pytest

from video_trimmer import core
from video_trimmer.core import ExportCancelled, Mode, OutputKind, Segment

try:
    FFMPEG = core.ensure_ffmpeg()[0]
except core.TrimmerError:
    FFMPEG = None

pytestmark = pytest.mark.skipif(FFMPEG is None, reason="FFmpeg not installed")


def _ff(*args):
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", *args], check=True)


def _make(path, audio=True):
    cmd = ["-f", "lavfi", "-i", "testsrc=duration=6:size=161x121:rate=25"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=duration=6", "-c:a", "aac", "-shortest"]
    _ff(*cmd, "-c:v", "libx264", "-g", "25", str(path))


def _clean(folder):
    return not [p for p in folder.iterdir() if p.name.startswith(".vt_")]


@pytest.mark.parametrize("mode", [Mode.ACCURATE, Mode.FAST])
@pytest.mark.parametrize("audio", [True, False])
def test_merged_round_trip(tmp_path, mode, audio):
    src, dst = tmp_path / "in.mp4", tmp_path / "out.mp4"
    _make(src, audio)
    core.export(str(src), str(dst), [Segment(0, 2), Segment(3, 5)], mode)
    info = core.probe(str(dst))
    assert info.has_audio is audio and 3.5 <= info.duration <= 6.5 and _clean(tmp_path)


@pytest.mark.parametrize("mode", [Mode.ACCURATE, Mode.FAST])
@pytest.mark.parametrize("audio", [True, False])
def test_separate_round_trip_with_overlap(tmp_path, mode, audio):
    src, out = tmp_path / "in.mp4", tmp_path / "clips"
    out.mkdir()
    _make(src, audio)
    paths = core.export(
        str(src),
        str(out),
        [Segment(0, 3), Segment(2, 4)],
        mode,
        output=OutputKind.SEPARATE,
    )
    assert sorted(p.name for p in out.iterdir()) == ["in_clip001.mp4", "in_clip002.mp4"]
    for p in paths:
        assert core.probe(p).has_audio is audio
    assert _clean(out)


def test_accurate_cut_length_is_exact(tmp_path):
    src, dst = tmp_path / "in.mp4", tmp_path / "out.mkv"
    _make(src)
    core.export(str(src), str(dst), [Segment(1.2, 3.7)], Mode.ACCURATE)
    assert core.probe(str(dst)).duration == pytest.approx(2.5, abs=0.15)


def test_odd_dimensions_and_variable_frame_rate(tmp_path):
    src, dst = tmp_path / "vfr.mp4", tmp_path / "out.mp4"
    base = [
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=6:size=161x121:rate=30",
        "-vf",
        "select='lt(mod(n,7),4)'",
        "-c:v",
        "libx264",
    ]
    try:
        _ff(*base, "-fps_mode", "vfr", str(src))
    except subprocess.CalledProcessError:
        _ff(*base, "-vsync", "vfr", str(src))
    core.export(str(src), str(dst), [Segment(0, 1.5), Segment(2, 3)], Mode.ACCURATE)
    assert core.probe(str(dst)).duration > 1.5


def test_cancel_discards_output(tmp_path):
    src, dst = tmp_path / "in.mp4", tmp_path / "out.mp4"
    _make(src)
    token = core.CancelToken()
    with pytest.raises(ExportCancelled):
        core.export(
            str(src),
            str(dst),
            [Segment(0, 5)],
            Mode.ACCURATE,
            lambda f: token.cancel() if f > 0 else None,
            token,
        )
    assert not dst.exists() and _clean(tmp_path)


def test_corrupt_file(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video at all" * 10)
    with pytest.raises(core.TrimmerError, match="corrupted|not a video"):
        core.probe(str(bad))


def test_audio_only_file_rejected(tmp_path):
    wav = tmp_path / "a.wav"
    _ff("-f", "lavfi", "-i", "sine=duration=1", str(wav))
    with pytest.raises(core.TrimmerError, match="no video stream"):
        core.probe(str(wav))


@pytest.mark.parametrize("mode", [Mode.ACCURATE, Mode.FAST])
@pytest.mark.parametrize("audio", [True, False])
def test_cut_one_second_from_both_ends(tmp_path, mode, audio):
    """The reported scenario: remove the first and last second, keep everything in between."""
    src, dst = tmp_path / "in.mp4", tmp_path / "out.mp4"
    _make(src, audio)
    keep = core.invert_segments(
        [Segment(0, 1), Segment(5, 6)], core.probe(str(src)).duration
    )
    assert keep == [Segment(1, 5)]
    core.export(str(src), str(dst), keep, mode)
    info = core.probe(str(dst))
    assert info.has_audio is audio
    assert (
        (3.8 <= info.duration <= 4.3)
        if mode == Mode.ACCURATE
        else (3.8 <= info.duration <= 5.1)
    )


def test_cut_middle_merges_remaining_parts(tmp_path):
    src, dst = tmp_path / "in.mp4", tmp_path / "out.mp4"
    _make(src)
    keep = core.invert_segments([Segment(2, 4)], 6.0)
    core.export(str(src), str(dst), keep, Mode.ACCURATE)
    assert core.probe(str(dst)).duration == pytest.approx(4.0, abs=0.2)


def test_render_frame_returns_exact_png_frames(tmp_path):
    src = tmp_path / "in.mp4"
    _make(src)
    first, later = core.render_frame(str(src), 0.0), core.render_frame(str(src), 3.0)
    assert (
        first.startswith(b"\x89PNG") and later.startswith(b"\x89PNG") and first != later
    )
    assert core.render_frame(str(src), 6.0).startswith(
        b"\x89PNG"
    )  # the very end still yields a frame
