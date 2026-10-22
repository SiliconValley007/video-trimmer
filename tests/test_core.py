import json
from pathlib import Path
from unittest import mock

import pytest

from video_trimmer import core
from video_trimmer.core import (
    CancelToken, ExportCancelled, MediaInfo, Mode, OutputKind, Segment, TrimmerError,
)

S = Segment
VIDEO = {"format": {"duration": "12.5"},
         "streams": [{"codec_type": "video", "width": 640, "height": 360, "codec_name": "h264",
                      "avg_frame_rate": "30/1", "r_frame_rate": "30/1"}]}


# ---------------------------------------------------------------- discovery
def test_ensure_ffmpeg_missing():
    with mock.patch.object(core, "_find_tool", return_value=None):
        with pytest.raises(TrimmerError, match="FFmpeg not found"):
            core.ensure_ffmpeg()


def test_ensure_ffmpeg_reports_which_tool_is_missing():
    with mock.patch.object(core, "_find_tool", side_effect=lambda n: "/x/ffmpeg" if n == "ffmpeg" else None):
        with pytest.raises(TrimmerError, match="missing: ffprobe"):
            core.ensure_ffmpeg()


def test_ensure_ffmpeg_ok():
    with mock.patch.object(core, "_find_tool", side_effect=lambda n: f"/x/{n}"):
        assert core.ensure_ffmpeg() == ("/x/ffmpeg", "/x/ffprobe")


def test_find_tool_env_override(tmp_path, monkeypatch):
    exe = tmp_path / "ffmpeg"
    exe.write_text("")
    monkeypatch.setenv("FFMPEG_BINARY", str(exe))
    assert core._find_tool("ffmpeg") == str(exe)


def test_find_tool_path_then_none(monkeypatch):
    monkeypatch.delenv("FFPROBE_BINARY", raising=False)
    with mock.patch.object(core.shutil, "which", return_value="/bin/ffprobe"):
        assert core._find_tool("ffprobe") == "/bin/ffprobe"
    with mock.patch.object(core.shutil, "which", return_value=None), \
            mock.patch.object(core.Path, "is_file", return_value=False):
        assert core._find_tool("ffprobe") is None


# ---------------------------------------------------------------- probe
@pytest.fixture
def tools():
    with mock.patch.object(core, "ensure_ffmpeg", return_value=("ffmpeg", "ffprobe")):
        yield


def _file(tmp_path, name="v.mp4"):
    p = tmp_path / name
    p.write_bytes(b"x")
    return p


def _result(data=None, code=0, err="", raw=None):
    return mock.Mock(returncode=code, stdout=raw if raw is not None else json.dumps(data or {}), stderr=err)


def _probe(tmp_path, **kw):
    with mock.patch.object(core.subprocess, "run", return_value=_result(**kw)):
        return core.probe(str(_file(tmp_path)))


def test_probe_missing_file(tools, tmp_path):
    with pytest.raises(TrimmerError, match="File not found"):
        core.probe(str(tmp_path / "nope.mp4"))


def test_probe_no_read_permission(tools, tmp_path):
    with mock.patch.object(core.os, "access", return_value=False):
        with pytest.raises(TrimmerError, match="No read permission"):
            core.probe(str(_file(tmp_path)))


def test_probe_corrupt(tools, tmp_path):
    with pytest.raises(TrimmerError, match="corrupted"):
        _probe(tmp_path, code=1, err="x.mp4: Invalid data found when processing input")


def test_probe_garbage_output_and_errors(tools, tmp_path):
    with pytest.raises(TrimmerError, match="unreadable output"):
        _probe(tmp_path, raw="not json")
    for exc, msg in ((OSError("boom"), "Cannot run FFprobe"),
                     (core.subprocess.TimeoutExpired("ffprobe", 60), "Timed out")):
        with mock.patch.object(core.subprocess, "run", side_effect=exc):
            with pytest.raises(TrimmerError, match=msg):
                core.probe(str(_file(tmp_path)))


def test_probe_no_video_stream(tools, tmp_path):
    with pytest.raises(TrimmerError, match="no video stream"):
        _probe(tmp_path, data={"streams": [{"codec_type": "audio"}]})


def test_probe_metadata_video_only(tools, tmp_path):
    info = _probe(tmp_path, data=VIDEO)
    assert (info.duration, info.has_audio, info.width, info.height, info.fps) == (12.5, False, 640, 360, 30.0)
    assert not info.is_vfr and "no audio" in info.summary() and "30 fps" in info.summary()


def test_probe_detects_vfr(tools, tmp_path):
    data = {"format": {"duration": "5"},
            "streams": [{"codec_type": "video", "avg_frame_rate": "2997/100", "r_frame_rate": "90000/1"}]}
    info = _probe(tmp_path, data=data)
    assert info.is_vfr and info.fps == pytest.approx(29.97) and "VFR" in info.summary()
    ntsc = {"streams": [{"codec_type": "video", "avg_frame_rate": "30000/1001", "r_frame_rate": "30000/1001"}]}
    assert not _probe(tmp_path, data=ntsc).is_vfr


def test_probe_duration_fallback_unknown_and_bad_rates(tools, tmp_path):
    fallback = {"format": {"duration": "N/A"}, "streams": [{"codec_type": "video", "duration": "7"}]}
    assert _probe(tmp_path, data=fallback).duration == 7.0
    unknown = {"format": {}, "streams": [{"codec_type": "video", "avg_frame_rate": "0/0"},
                                         {"codec_type": "audio"}]}
    info = _probe(tmp_path, data=unknown)
    assert info.duration is None and info.has_audio and info.fps is None
    assert "unknown duration" in info.summary()


def test_rate_parsing():
    assert core._rate("30000/1001") == pytest.approx(29.97, rel=1e-3)
    assert core._rate("25") == 25
    for bad in ("0/0", "abc", None, "-1/1", "5/0"):
        assert core._rate(bad) is None


# ---------------------------------------------------------------- CREATE_NO_WINDOW
def test_probe_hides_console_window(tools, tmp_path):
    with mock.patch.object(core.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
            mock.patch.object(core.subprocess, "run", return_value=_result(VIDEO)) as run:
        core.probe(str(_file(tmp_path)))
    assert run.call_args.kwargs["creationflags"] == 0x08000000


def test_run_hides_console_window():
    with mock.patch.object(core.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
            mock.patch.object(core.subprocess, "Popen", return_value=FakeProc()) as popen:
        core._run(_stream(), "ffmpeg", 1, None, CancelToken())
    assert popen.call_args.kwargs["creationflags"] == 0x08000000


def test_no_window_flag_is_zero_without_windows():
    with mock.patch.object(core, "subprocess", mock.Mock(spec=[])):
        assert core._no_window() == 0


# ---------------------------------------------------------------- validation
def test_find_issues_ok_and_touching():
    assert core.find_issues([S(0, 5), S(5, 9)], 10) == {}


def test_find_issues_cases():
    assert 0 in core.find_issues([S(5, 5)], 10)
    assert "length" in core.find_issues([S(0, 11)], 10)[0]
    assert core.find_issues([S(0, 6), S(5, 9)], 10) == {1: "Overlaps segment 1."}
    assert core.find_issues([S(0, 10), S(2, 3)], 10) == {1: "Overlaps segment 1."}
    assert core.find_issues([None, S(0, 1)], 10) == {}
    assert core.find_issues([S(0, 1000)], None) == {}


def test_overlap_allowed_for_separate_clips_but_bounds_still_checked():
    assert core.find_issues([S(0, 6), S(5, 9)], 10, allow_overlap=True) == {}
    assert 1 in core.find_issues([S(0, 6), S(5, 99)], 10, allow_overlap=True)


def test_validate_segments():
    with pytest.raises(TrimmerError, match="at least one"):
        core.validate_segments([], 10)
    with pytest.raises(TrimmerError, match="Segment 1"):
        core.validate_segments([S(3, 2)], 10)
    core.validate_segments([S(0, 1)], 10)
    core.validate_segments([S(0, 6), S(5, 9)], 10, allow_overlap=True)


def test_media_info_summary_and_segment_length():
    assert S(1, 3.5).length == 2.5
    assert "640\u00d7360" in MediaInfo(5, True, 640, 360, "h264").summary()


# ---------------------------------------------------------------- naming
def test_separate_paths_and_default_suffix(tmp_path):
    names = [p.name for p in core.separate_paths("/a/movie.MKV", str(tmp_path), 2)]
    assert names == ["movie_clip001.mkv", "movie_clip002.mkv"]
    assert core.default_suffix("x.avi") == ".mp4"
    assert core.separate_paths("v.mp4", ".", 1000)[-1].name == "v_clip1000.mp4"


# ---------------------------------------------------------------- export (mocked FFmpeg)
class Recorder:
    """Stand-in for core._run: records graphs and writes the output file."""

    def __init__(self, fail=None, fail_at=0):
        self.args, self.fail, self.fail_at = [], fail, fail_at

    def __call__(self, stream, ffmpeg_bin, total, on_progress, token):
        self.args.append(stream.get_args())
        if self.fail and len(self.args) > self.fail_at:
            raise self.fail
        Path(stream.node.kwargs["filename"]).write_bytes(b"video")
        if on_progress:
            on_progress(1.0)


@pytest.fixture
def env(tools, tmp_path):
    src = _file(tmp_path)
    info = MediaInfo(60.0, True, 640, 360, "h264", 30.0, False)
    with mock.patch.object(core, "probe", return_value=info):
        yield src, tmp_path / "out.mp4", tmp_path


def _leftovers(folder):
    return [p for p in folder.iterdir() if p.name.startswith(".vt_")]


def test_export_accurate_with_audio(env):
    src, dst, _ = env
    rec, prog = Recorder(), []
    with mock.patch.object(core, "_run", rec):
        out = core.export(str(src), str(dst), [S(0, 5), S(10, 20)], Mode.ACCURATE, prog.append)
    args = " ".join(rec.args[0])
    assert out == [str(dst.resolve())] and dst.read_bytes() == b"video" and not _leftovers(dst.parent)
    assert "concat=a=1:n=2:v=1" in args and "libx264" in args and "aac" in args and "faststart" in args
    assert "fps=fps" not in args
    assert prog[0] == 0.0 and prog[-1] == 1.0


def test_export_accurate_video_only(env):
    src, dst, _ = env
    rec = Recorder()
    with mock.patch.object(core, "probe", return_value=MediaInfo(60.0, False)), mock.patch.object(core, "_run", rec):
        core.export(str(src), str(dst), [S(0, 5)])
    args = " ".join(rec.args[0])
    assert "a=0" in args and "aac" not in args and "asetpts" not in args


def test_export_vfr_is_normalised_to_cfr(env):
    src, dst, _ = env
    rec = Recorder()
    with mock.patch.object(core, "probe", return_value=MediaInfo(60.0, True, fps=29.97, is_vfr=True)), \
            mock.patch.object(core, "_run", rec):
        core.export(str(src), str(dst), [S(0, 5)])
    assert "fps=fps=29.97000" in " ".join(rec.args[0])


def test_export_mkv_has_no_faststart(env):
    src, _, tmp = env
    rec = Recorder()
    with mock.patch.object(core, "_run", rec):
        core.export(str(src), str(tmp / "o.mkv"), [S(0, 5)])
    assert "faststart" not in " ".join(rec.args[0])


def test_export_fast_multiple_and_single(env):
    src, dst, _ = env
    rec = Recorder()
    with mock.patch.object(core, "_run", rec):
        core.export(str(src), str(dst), [S(0, 5), S(10, 20)], Mode.FAST)
    assert len(rec.args) == 3  # two cuts + concat
    assert all("copy" in a for a in rec.args) and "concat" in rec.args[2]
    rec2 = Recorder()
    with mock.patch.object(core, "_run", rec2):
        core.export(str(src), str(dst), [S(0, 5)], Mode.FAST)
    assert len(rec2.args) == 1 and not _leftovers(dst.parent)


def test_export_fast_video_only_maps_only_video(env):
    src, dst, _ = env
    rec = Recorder()
    with mock.patch.object(core, "probe", return_value=MediaInfo(60.0, False)), mock.patch.object(core, "_run", rec):
        core.export(str(src), str(dst), [S(0, 5)], Mode.FAST)
    args = rec.args[0]
    assert "0:v:0" in args and "0:a:0" not in args


@pytest.mark.parametrize("mode", [Mode.ACCURATE, Mode.FAST])
def test_export_separate_files(env, mode):
    src, _, tmp = env
    out_dir = tmp / "clips"
    out_dir.mkdir()
    rec, prog = Recorder(), []
    with mock.patch.object(core, "_run", rec):
        out = core.export(str(src), str(out_dir), [S(0, 6), S(5, 9)], mode, prog.append, output=OutputKind.SEPARATE)
    assert [Path(p).name for p in out] == ["v_clip001.mp4", "v_clip002.mp4"]  # overlap is fine here
    assert all((out_dir / Path(p).name).read_bytes() == b"video" for p in out)
    assert len(rec.args) == 2 and not _leftovers(out_dir)
    assert prog == sorted(prog) and prog[-1] == 1.0


def test_export_separate_errors(env):
    src, _, tmp = env
    with pytest.raises(TrimmerError, match="folder does not exist"):
        core.export(str(src), str(tmp / "missing"), [S(0, 5)], output=OutputKind.SEPARATE)
    with pytest.raises(TrimmerError, match="after the video length"):
        core.export(str(src), str(tmp), [S(0, 99)], output=OutputKind.SEPARATE)


def test_export_validation_errors(env):
    src, dst, _ = env
    with pytest.raises(TrimmerError, match="after the video length"):
        core.export(str(src), str(dst), [S(0, 99)])
    with pytest.raises(TrimmerError, match="overlap|Overlaps"):
        core.export(str(src), str(dst), [S(0, 6), S(5, 9)])
    with pytest.raises(TrimmerError, match="differ from the source"):
        core.export(str(src), str(src), [S(0, 5)])
    with pytest.raises(TrimmerError, match="Unsupported output"):
        core.export(str(src), str(dst.with_suffix(".avi")), [S(0, 5)])


def test_export_permission_errors(env):
    src, dst, _ = env
    with mock.patch.object(core.os, "access", side_effect=lambda p, m: Path(p) == src):
        with pytest.raises(TrimmerError, match="No write permission"):
            core.export(str(src), str(dst), [S(0, 5)])
    dst.write_bytes(b"old")
    with mock.patch.object(core.os, "access", side_effect=lambda p, m: Path(p) != dst):
        with pytest.raises(TrimmerError, match="existing file"):
            core.export(str(src), str(dst), [S(0, 5)])
    with mock.patch.object(core.tempfile, "mkdtemp", side_effect=PermissionError(13, "denied")):
        with pytest.raises(TrimmerError, match="temporary file"):
            core.export(str(src), str(dst), [S(0, 5)])


def test_export_failure_cleans_up_and_keeps_existing_output(env):
    src, dst, _ = env
    dst.write_bytes(b"old")
    with mock.patch.object(core, "_run", Recorder(fail=TrimmerError("FFmpeg failed"))):
        with pytest.raises(TrimmerError, match="FFmpeg failed"):
            core.export(str(src), str(dst), [S(0, 5)])
    assert dst.read_bytes() == b"old" and not _leftovers(dst.parent)


def test_export_cancel_cleans_up(env):
    src, dst, _ = env
    with mock.patch.object(core, "_run", Recorder(fail=ExportCancelled())):
        with pytest.raises(ExportCancelled):
            core.export(str(src), str(dst), [S(0, 5), S(6, 8)], Mode.FAST)
    assert not dst.exists() and not _leftovers(dst.parent)


def test_export_separate_cancel_midway_leaves_nothing(env):
    src, _, tmp = env
    out_dir = tmp / "clips"
    out_dir.mkdir()
    with mock.patch.object(core, "_run", Recorder(fail=ExportCancelled(), fail_at=1)):
        with pytest.raises(ExportCancelled):
            core.export(str(src), str(out_dir), [S(0, 5), S(6, 8)], output=OutputKind.SEPARATE)
    assert list(out_dir.iterdir()) == []


def test_export_cancelled_by_token_before_replace(env):
    src, dst, _ = env
    token = CancelToken()

    def cancelling(stream, *_a):
        Path(stream.node.kwargs["filename"]).write_bytes(b"v")
        token.cancel()

    with mock.patch.object(core, "_run", cancelling):
        with pytest.raises(ExportCancelled):
            core.export(str(src), str(dst), [S(0, 5)], token=token)
    assert not dst.exists() and not _leftovers(dst.parent)


def test_export_replace_failure(env):
    src, dst, _ = env
    with mock.patch.object(core, "_run", Recorder()), \
            mock.patch.object(core.os, "replace", side_effect=PermissionError(13, "Permission denied")):
        with pytest.raises(TrimmerError, match="Could not write"):
            core.export(str(src), str(dst), [S(0, 5)])
    assert not _leftovers(dst.parent)


# ---------------------------------------------------------------- _run with a fake process
class FakeProc:
    def __init__(self, out=(), err=(), code=0):
        self.stdout, self.stderr, self.code, self.terminated = iter(out), iter(err), code, False

    def wait(self):
        return self.code

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True

    def kill(self):
        pass


def _stream():
    return core.ffmpeg.input("in.mp4").output("out.mp4")


def test_run_uses_progress_pipe_and_reports_progress():
    proc = FakeProc(out=["out_time_us=5000000\n", "out_time_us=N/A\n", "out_time_ms=20000000\n", "progress=end\n"])
    seen = []
    with mock.patch.object(core.subprocess, "Popen", return_value=proc) as popen:
        core._run(_stream(), "ffmpeg", 10.0, seen.append, CancelToken())
    cmd = popen.call_args.args[0]
    assert cmd[:2] == ["ffmpeg", "-hide_banner"] and "-progress" in cmd and "pipe:1" in cmd
    assert seen == [0.5, 1.0]


@pytest.mark.parametrize("stderr,expected", [
    ("out.mp4: Permission denied", "Permission denied while accessing"),
    ("No space left on device", "disk is full"),
    ("Unknown encoder 'libx264'", "lacks the H.264 encoder"),
    ("moov atom not found", "incomplete or corrupted"),
    ("Could not find tag for codec pcm in stream", "cannot be stored"),
    ("something odd", "something odd"),
])
def test_run_failure_maps_hint(stderr, expected):
    with mock.patch.object(core.subprocess, "Popen", return_value=FakeProc(err=[stderr + "\n"], code=1)):
        with pytest.raises(TrimmerError, match=expected):
            core._run(_stream(), "ffmpeg", 1, None, CancelToken())


def test_run_cannot_start():
    with mock.patch.object(core.subprocess, "Popen", side_effect=FileNotFoundError(2, "No such file")):
        with pytest.raises(TrimmerError, match="Could not start FFmpeg"):
            core._run(_stream(), "ffmpeg", 1, None, CancelToken())


def test_run_cancelled_before_start_and_after_run():
    token = CancelToken()
    token.cancel()
    with pytest.raises(ExportCancelled):
        core._run(_stream(), "ffmpeg", 1, None, token)
    token2, proc = CancelToken(), FakeProc(out=["out_time_us=1000000\n"], code=-15)
    with mock.patch.object(core.subprocess, "Popen", return_value=proc):
        with pytest.raises(ExportCancelled):
            core._run(_stream(), "ffmpeg", 2, lambda _f: token2.cancel(), token2)


def test_cancel_token_terminates_attached_process():
    class Live(FakeProc):
        def poll(self):
            return None

    token, proc = CancelToken(), Live()
    token.attach(proc)
    token.cancel()
    assert proc.terminated and token.cancelled
    late = Live()
    token.attach(late)
    assert late.terminated
    token.detach()


# ---------------------------------------------------------------- cut-out (remove) semantics
def test_invert_trims_start_and_end():
    assert core.invert_segments([S(0, 1), S(59, 60)], 60) == [S(1, 59)]


def test_invert_middle_cuts_and_merging():
    assert core.invert_segments([S(10, 20), S(30, 40)], 60) == [S(0, 10), S(20, 30), S(40, 60)]
    assert core.invert_segments([S(5, 10), S(8, 12), S(12, 13)], 20) == [S(0, 5), S(13, 20)]  # overlap + touching
    assert core.invert_segments([S(30, 40), S(10, 20)], 60) == [S(0, 10), S(20, 30), S(40, 60)]  # order-independent


def test_invert_edge_cases():
    assert core.invert_segments([], 10) == [S(0, 10)]
    assert core.invert_segments([S(0, 10)], 10) == []
    assert core.invert_segments([S(-5, 3), S(8, 99)], 10) == [S(3, 8)]  # clipped to the video
    assert core.invert_segments([S(20, 30)], 10) == [S(0, 10)]  # entirely outside
    assert core.invert_segments([S(0, 4.9995)], 10) == [S(4.9995, 10)]
    assert core.invert_segments([S(0, 9.9995)], 10) == []  # sliver below tolerance is dropped
    for bad in (None, 0):
        with pytest.raises(TrimmerError, match="duration is unknown"):
            core.invert_segments([S(0, 1)], bad)


def test_bundled_ffmpeg_is_found_next_to_frozen_exe(tmp_path, monkeypatch):
    exe = "ffmpeg.exe" if core.os.name == "nt" else "ffmpeg"
    (tmp_path / exe).write_text("")
    monkeypatch.delenv("FFMPEG_BINARY", raising=False)
    monkeypatch.setattr(core.sys, "frozen", True, raising=False)
    monkeypatch.setattr(core.sys, "executable", str(tmp_path / "VideoTrimmer.exe"))
    monkeypatch.setattr(core.sys, "_MEIPASS", str(tmp_path / "none"), raising=False)
    with mock.patch.object(core.shutil, "which", return_value="/elsewhere/ffmpeg"):
        assert core._find_tool("ffmpeg") == str(tmp_path / exe)


# ---------------------------------------------------------------- preview frame rendering
class PngProc:
    def __init__(self, out=b"\x89PNG-data", code=0, raises=None):
        self.out, self.returncode, self.raises, self.killed = out, code, raises, False

    def communicate(self, timeout=None):
        if self.raises:
            exc, self.raises = self.raises, None
            raise exc
        return self.out, b""

    def kill(self):
        self.killed = True

    def poll(self):
        return None

    def terminate(self):
        pass


def _popen(*procs):
    return mock.patch.object(core.subprocess, "Popen", side_effect=list(procs))


def _arg_after(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def test_render_frame_command_line():
    with _popen(PngProc()) as popen:
        data = core.render_frame("in.mp4", 1.5, "ffmpeg")
    cmd = popen.call_args.args[0]
    assert data.startswith(b"\x89PNG")
    assert cmd[0] == "ffmpeg" and cmd.index("-ss") < cmd.index("-i") and _arg_after(cmd, "-ss") == "1.500"
    assert _arg_after(cmd, "-i") == "in.mp4" and _arg_after(cmd, "-frames:v") == "1"
    assert _arg_after(cmd, "-vf") == "scale=640:-1" and _arg_after(cmd, "-f") == "image2pipe"
    assert _arg_after(cmd, "-vcodec") == "png" and cmd[-1] == "-"


@pytest.mark.parametrize("seconds,expected", [
    (2 / 30, "0.066"), (1.0, "1.000"), (0.1 + 0.2, "0.300"), (-3, "0.000"), (12.3456, "12.345"),
])
def test_render_frame_floors_to_millisecond_so_the_exact_frame_is_chosen(seconds, expected):
    with _popen(PngProc()) as popen:
        core.render_frame("in.mp4", seconds, "ffmpeg")
    assert _arg_after(popen.call_args.args[0], "-ss") == expected


def test_render_frame_custom_width():
    with _popen(PngProc()) as popen:
        core.render_frame("in.mp4", 1, "ffmpeg", width=320)
    assert _arg_after(popen.call_args.args[0], "-vf") == "scale=320:-1"


def test_render_frame_retries_just_before_the_end_then_gives_up():
    with _popen(PngProc(out=b""), PngProc()) as popen:
        assert core.render_frame("in.mp4", 5.0, "ffmpeg").startswith(b"\x89PNG")
    assert [_arg_after(c.args[0], "-ss") for c in popen.call_args_list] == ["5.000", "4.750"]
    with _popen(PngProc(out=b""), PngProc(code=1)):
        with pytest.raises(TrimmerError, match="No frame"):
            core.render_frame("in.mp4", 5.0, "ffmpeg")
    with _popen(PngProc(out=b"")) as popen:  # at t=0 there is nothing earlier to retry
        with pytest.raises(TrimmerError):
            core.render_frame("in.mp4", 0, "ffmpeg")
    assert popen.call_count == 1


def test_render_frame_hides_console_window_and_resolves_ffmpeg():
    with mock.patch.object(core.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
            mock.patch.object(core, "ensure_ffmpeg", return_value=("/x/ffmpeg", "/x/ffprobe")), \
            _popen(PngProc()) as popen:
        core.render_frame("in.mp4", 1)
    assert popen.call_args.kwargs["creationflags"] == 0x08000000 and popen.call_args.args[0][0] == "/x/ffmpeg"


def test_render_frame_failure_modes():
    with mock.patch.object(core.subprocess, "Popen", side_effect=FileNotFoundError(2, "missing")):
        with pytest.raises(TrimmerError, match="Could not start FFmpeg"):
            core.render_frame("in.mp4", 1, "ffmpeg")
    stuck = PngProc(raises=core.subprocess.TimeoutExpired("ffmpeg", 1))
    with _popen(stuck):
        with pytest.raises(TrimmerError, match="Timed out"):
            core.render_frame("in.mp4", 1, "ffmpeg", timeout=1)
    assert stuck.killed
    token = CancelToken()
    token.cancel()
    with pytest.raises(ExportCancelled):
        core.render_frame("in.mp4", 1, "ffmpeg", token=token)


def test_render_frame_cancelled_while_running():
    token = CancelToken()

    class Cancelling(PngProc):
        def communicate(self, timeout=None):
            token.cancel()
            return b"", b""

    with _popen(Cancelling()):
        with pytest.raises(ExportCancelled):
            core.render_frame("in.mp4", 1, "ffmpeg", token=token)
