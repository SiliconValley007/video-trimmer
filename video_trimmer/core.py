"""FFmpeg-backed trimming engine. Has no GUI dependencies."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Sequence

import ffmpeg

from .timeparse import format_time

SUPPORTED_OUT = (".mp4", ".mkv", ".mov")
EPS = 1e-3
ProgressCallback = Callable[[float], None]

_HINTS = (
    (
        "permission denied",
        "Permission denied while accessing a file. Check your access rights.",
    ),
    ("no space left", "The disk is full. Free some space and try again."),
    ("invalid data found", "The source file appears to be corrupted or unsupported."),
    (
        "moov atom not found",
        "The source file is incomplete or corrupted (missing index).",
    ),
    (
        "unknown encoder",
        "This FFmpeg build lacks the H.264 encoder (libx264). Install a full FFmpeg build or use Fast Cut.",
    ),
    (
        "encoder libx264 not found",
        "This FFmpeg build lacks the H.264 encoder (libx264). Install a full FFmpeg build or use Fast Cut.",
    ),
    (
        "decoder",
        "FFmpeg cannot decode this video's codec. Try another file or a fuller FFmpeg build.",
    ),
    (
        "no such file or directory",
        "A file or folder disappeared during export. Check the paths and try again.",
    ),
    (
        "could not find tag for codec",
        "The source codec cannot be stored in this output format. "
        "Choose .mkv or use Frame-Accurate mode.",
    ),
)


class TrimmerError(Exception):
    """User-facing, actionable error."""


class ExportCancelled(Exception):
    """Raised when the user cancels an export."""


class Mode(str, Enum):
    """Cutting strategy."""

    ACCURATE = "accurate"  # re-encode, frame-accurate
    FAST = "fast"  # stream copy, keyframe-snapped


class OutputKind(str, Enum):
    """Output layout."""

    MERGED = "merged"  # one concatenated file
    SEPARATE = "separate"  # one file per clip


class Action(str, Enum):
    """What the listed segments mean."""

    REMOVE = "remove"  # cut these parts out, keep the rest
    KEEP = "keep"  # keep only these parts


@dataclass(frozen=True)
class Segment:
    start: float
    end: float

    @property
    def length(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class MediaInfo:
    duration: Optional[float]
    has_audio: bool
    width: Optional[int] = None
    height: Optional[int] = None
    video_codec: Optional[str] = None
    fps: Optional[float] = None
    is_vfr: bool = False

    def summary(self) -> str:
        parts = []
        if self.width and self.height:
            parts.append(f"{self.width}\u00d7{self.height}")
        if self.video_codec:
            parts.append(self.video_codec)
        if self.fps:
            parts.append(f"{self.fps:.4g} fps" + (" (VFR)" if self.is_vfr else ""))
        parts.append(
            format_time(self.duration) if self.duration else "unknown duration"
        )
        parts.append("audio" if self.has_audio else "no audio")
        return "  \u2022  ".join(parts)


class CancelToken:
    """Thread-safe cancellation handle that also terminates the running FFmpeg process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = False
        self._proc: Optional[subprocess.Popen] = None

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            proc = self._proc
        self._terminate(proc)

    def attach(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._proc = proc
            cancelled = self._cancelled
        if cancelled:
            self._terminate(proc)

    def detach(self) -> None:
        with self._lock:
            self._proc = None

    def check(self) -> None:
        if self._cancelled:
            raise ExportCancelled()

    @staticmethod
    def _terminate(proc: Optional[subprocess.Popen]) -> None:
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass


# ---------------------------------------------------------------- FFmpeg discovery
def _bundled_dirs() -> list[Path]:
    """Folders next to (or inside) a frozen executable where FFmpeg may be shipped."""
    dirs = []
    if getattr(sys, "_MEIPASS", None):
        dirs.append(Path(sys._MEIPASS))  # type: ignore[attr-defined]
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).resolve().parent)
    return dirs


def _find_tool(name: str) -> Optional[str]:
    """Locate an FFmpeg tool: env override, bundled copy, PATH, then common install folders."""
    env = os.environ.get(f"{name.upper()}_BINARY")
    if env and Path(env).is_file():
        return env
    exe = name + (".exe" if os.name == "nt" else "")
    for folder in _bundled_dirs():
        if (folder / exe).is_file():
            return str(folder / exe)
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if local and packages.is_dir():
            for hit in packages.glob(f"*FFmpeg*/**/bin/{exe}"):
                return str(hit)
        folders = [
            Path(local) / "Microsoft" / "WinGet" / "Links" if local else None,
            Path("C:/ffmpeg/bin"),
            Path("C:/Program Files/ffmpeg/bin"),
        ]
    else:
        folders = [
            Path(p)
            for p in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/snap/bin")
        ]
    for folder in filter(None, folders):
        if (folder / exe).is_file():
            return str(folder / exe)
    return None


def ensure_ffmpeg() -> tuple[str, str]:
    """Return ``(ffmpeg, ffprobe)`` paths or raise an actionable error."""
    paths = {t: _find_tool(t) for t in ("ffmpeg", "ffprobe")}
    missing = [t for t, p in paths.items() if p is None]
    if missing:
        raise TrimmerError(
            f"FFmpeg not found (missing: {', '.join(missing)}).\n"
            "Install FFmpeg and add it to PATH (see README), restart your terminal/IDE, "
            "or set FFMPEG_BINARY and FFPROBE_BINARY to the full executable paths."
        )
    return paths["ffmpeg"], paths["ffprobe"]  # type: ignore[return-value]


# ---------------------------------------------------------------- probing
def _tail(raw: object) -> str:
    text = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw or "")
    lines = text.strip().splitlines()
    return "\n".join(lines[-5:]) or "no details available"


def _explain(tail: str) -> str:
    low = tail.lower()
    for needle, hint in _HINTS:
        if needle in low:
            return f"{hint}\n\n{tail}"
    return tail


def _no_window() -> int:
    """``CREATE_NO_WINDOW`` on Windows (0 elsewhere) so FFmpeg never flashes a console."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _rate(text: object) -> Optional[float]:
    """Parse an FFprobe frame-rate string such as ``30000/1001``."""
    try:
        num, _, den = str(text).partition("/")
        value = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def probe(path: str) -> MediaInfo:
    """Read metadata from a video file."""
    _, ffprobe = ensure_ffmpeg()
    p = Path(path)
    if not p.is_file():
        raise TrimmerError(f"File not found: {p}")
    if not os.access(p, os.R_OK):
        raise TrimmerError(f"No read permission for: {p}")
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(p),
    ]
    try:
        res = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=_no_window(),
        )
    except subprocess.TimeoutExpired as e:
        raise TrimmerError(f"Timed out while reading '{p.name}'.") from e
    except OSError as e:
        raise TrimmerError(f"Cannot run FFprobe: {e.strerror or e}") from e
    if res.returncode != 0:
        raise TrimmerError(
            f"Cannot read '{p.name}'. The file may be corrupted or not a video.\n{_explain(_tail(res.stderr))}"
        )
    try:
        data = json.loads(res.stdout or "{}")
    except ValueError as e:
        raise TrimmerError(f"FFprobe returned unreadable output for '{p.name}'.") from e
    streams = data.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video"]
    if not videos:
        raise TrimmerError(f"'{p.name}' contains no video stream.")
    duration = None
    for value in (
        data.get("format", {}).get("duration"),
        *(s.get("duration") for s in videos),
    ):
        try:
            d = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(d) and d > 0:
            duration = d
            break
    v = videos[0]
    avg, real = _rate(v.get("avg_frame_rate")), _rate(v.get("r_frame_rate"))
    return MediaInfo(
        duration=duration,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        width=v.get("width"),
        height=v.get("height"),
        video_codec=v.get("codec_name"),
        fps=avg or real,
        is_vfr=bool(avg and real and abs(avg - real) / real > 0.01),
    )


# ---------------------------------------------------------------- validation
def find_issues(
    segments: Sequence[Optional[Segment]],
    duration: Optional[float],
    allow_overlap: bool = False,
) -> dict[int, str]:
    """Map 0-based segment index to a problem description. ``None`` entries are skipped.

    Overlaps are only a problem when clips are merged (``allow_overlap=False``).
    """
    issues: dict[int, str] = {}
    for i, s in enumerate(segments):
        if s is None:
            continue
        if s.end <= s.start:
            issues[i] = "End must be after start."
        elif duration is not None and s.end > duration + EPS:
            issues[i] = f"Ends after the video length ({format_time(duration)})."
    if allow_overlap:
        return issues
    ordered = sorted(
        ((i, s) for i, s in enumerate(segments) if s is not None and s.end > s.start),
        key=lambda t: t[1].start,
    )
    max_end, max_idx = -math.inf, -1
    for i, s in ordered:
        if s.start < max_end - EPS:
            issues.setdefault(i, f"Overlaps segment {max_idx + 1}.")
        if s.end > max_end:
            max_end, max_idx = s.end, i
    return issues


def invert_segments(
    cuts: Sequence[Segment], duration: Optional[float]
) -> list[Segment]:
    """Return the parts of ``[0, duration]`` that remain after cutting out ``cuts``.

    Overlapping or touching cuts are merged; cuts outside the video are clipped.
    """
    if duration is None or duration <= 0:
        raise TrimmerError(
            "The video duration is unknown, so parts cannot be cut out. "
            "Use 'Keep only these segments' instead."
        )
    merged: list[list[float]] = []
    for c in sorted(cuts, key=lambda s: s.start):
        start, end = max(c.start, 0.0), min(c.end, duration)
        if end <= start:
            continue
        if merged and start <= merged[-1][1] + EPS:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    keep: list[Segment] = []
    pos = 0.0
    for start, end in merged:
        if start - pos > EPS:
            keep.append(Segment(pos, start))
        pos = end
    if duration - pos > EPS:
        keep.append(Segment(pos, duration))
    return keep


def validate_segments(
    segments: Sequence[Segment], duration: Optional[float], allow_overlap: bool = False
) -> None:
    if not segments:
        raise TrimmerError("Add at least one segment.")
    issues = find_issues(segments, duration, allow_overlap)
    if issues:
        raise TrimmerError(
            "\n".join(f"Segment {i + 1}: {m}" for i, m in sorted(issues.items()))
        )


# ---------------------------------------------------------------- process runner
def _run(
    stream: "ffmpeg.nodes.OutputStream",
    ffmpeg_bin: str,
    total: float,
    on_progress: Optional[ProgressCallback],
    token: CancelToken,
) -> None:
    """Run one FFmpeg job, reporting progress (0..1) and honouring cancellation."""
    args = [
        ffmpeg_bin,
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-progress",
        "pipe:1",
        *stream.overwrite_output().get_args(),
    ]
    token.check()
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_no_window(),
        )
    except OSError as e:
        raise TrimmerError(f"Could not start FFmpeg: {e.strerror or e}") from e

    tail: deque[str] = deque(maxlen=12)

    def drain() -> None:
        for line in proc.stderr:  # type: ignore[union-attr]
            tail.append(line.rstrip())

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    token.attach(proc)
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            key, _, value = line.strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and on_progress and total > 0:
                try:
                    seconds = int(value) / 1_000_000
                except ValueError:
                    continue
                on_progress(min(max(seconds / total, 0.0), 1.0))
        code = proc.wait()
    finally:
        token.detach()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        reader.join(timeout=2)
    token.check()
    if code != 0:
        raise TrimmerError("FFmpeg failed:\n" + _explain("\n".join(list(tail)[-6:])))


# ---------------------------------------------------------------- preview frames
def _grab_frame(
    src: str,
    pos: float,
    ffmpeg_bin: str,
    width: int,
    token: CancelToken,
    timeout: float,
) -> bytes:
    args = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-ss",
        f"{pos:.3f}",
        "-i",
        src,
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:-1",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-",
    ]
    token.check()
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_no_window(),
        )
    except OSError as e:
        raise TrimmerError(f"Could not start FFmpeg: {e.strerror or e}") from e
    token.attach(proc)
    try:
        out, _err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        proc.kill()
        proc.communicate()
        raise TrimmerError("Timed out while rendering the preview frame.") from e
    finally:
        token.detach()
    token.check()
    return out if proc.returncode == 0 and out.startswith(b"\x89PNG") else b""


def render_frame(
    src: str,
    seconds: float,
    ffmpeg_bin: Optional[str] = None,
    width: int = 640,
    token: Optional[CancelToken] = None,
    timeout: float = 20.0,
) -> bytes:
    """Decode exactly one frame at ``seconds`` and return it as PNG bytes.

    The time is floored to the millisecond so a frame boundary (e.g. 2/30 s) never
    lands on the following frame. Positions at the very end of the media retry slightly earlier.
    """
    token = token or CancelToken()
    ffmpeg_bin = ffmpeg_bin or ensure_ffmpeg()[0]
    first = math.floor(max(seconds, 0.0) * 1000 + 1e-6) / 1000
    for pos in dict.fromkeys((first, max(first - 0.25, 0.0))):
        data = _grab_frame(src, pos, ffmpeg_bin, width, token, timeout)
        if data:
            return data
    raise TrimmerError("No frame could be decoded at that position.")


# ---------------------------------------------------------------- export
def _accurate(src: str, segs: Sequence[Segment], info: MediaInfo, out: str):
    parts = []
    for s in segs:
        inp = ffmpeg.input(src, ss=f"{s.start:.3f}", t=f"{s.length:.3f}")
        parts.append(inp.video.filter("setpts", "PTS-STARTPTS"))
        if info.has_audio:
            parts.append(inp.audio.filter("asetpts", "PTS-STARTPTS"))
    joined = ffmpeg.concat(*parts, v=1, a=int(info.has_audio)).node
    video = joined[0].filter("scale", "trunc(iw/2)*2", "trunc(ih/2)*2")
    if info.is_vfr and info.fps:  # normalise variable frame rate to constant
        video = video.filter("fps", fps=f"{info.fps:.5f}")
    outs = [video] + ([joined[1]] if info.has_audio else [])
    opts = {"vcodec": "libx264", "pix_fmt": "yuv420p", "preset": "veryfast", "crf": 18}
    if info.has_audio:
        opts["acodec"] = "aac"
    if Path(out).suffix.lower() in (".mp4", ".mov"):
        opts["movflags"] = "+faststart"
    return ffmpeg.output(*outs, out, **opts)


def _copy_part(src: str, seg: Segment, info: MediaInfo, out: str):
    inp = ffmpeg.input(src, ss=f"{seg.start:.3f}", t=f"{seg.length:.3f}")
    outs = [inp["v:0"]] + ([inp["a:0"]] if info.has_audio else [])
    return ffmpeg.output(*outs, out, c="copy", avoid_negative_ts="make_zero")


def _fast(
    src, segs, info, work: Path, final: Path, ffmpeg_bin, total, report, token
) -> None:
    if len(segs) == 1:
        _run(
            _copy_part(src, segs[0], info, str(final)),
            ffmpeg_bin,
            segs[0].length,
            report,
            token,
        )
        return
    names, done = [], 0.0
    for i, seg in enumerate(segs):
        token.check()
        part = work / f"seg{i:04d}{final.suffix}"

        def cb(f: float, base: float = done, seg: Segment = seg) -> None:
            report((base + f * seg.length) / total * 0.98)

        _run(_copy_part(src, seg, info, str(part)), ffmpeg_bin, seg.length, cb, token)
        names.append(part.name)
        done += seg.length
    listing = work / "list.txt"
    listing.write_text("".join(f"file '{n}'\n" for n in names), encoding="utf-8")
    joined = ffmpeg.input(str(listing), f="concat", safe=0).output(str(final), c="copy")
    _run(joined, ffmpeg_bin, 0, None, token)


def default_suffix(src: str) -> str:
    """Container for separate clips: the source's own if supported, else ``.mp4``."""
    suffix = Path(src).suffix.lower()
    return suffix if suffix in SUPPORTED_OUT else ".mp4"


def separate_paths(src: str, folder: str, count: int) -> list[Path]:
    """Output file names for ``count`` separate clips."""
    width = max(3, len(str(count)))
    stem = Path(src).stem
    return [
        Path(folder) / f"{stem}_clip{i:0{width}d}{default_suffix(src)}"
        for i in range(1, count + 1)
    ]


def export(
    src: str,
    dst: str,
    segments: Sequence[Segment],
    mode: Mode = Mode.ACCURATE,
    on_progress: Optional[ProgressCallback] = None,
    token: Optional[CancelToken] = None,
    output: OutputKind = OutputKind.MERGED,
) -> list[str]:
    """Cut ``segments`` from ``src``.

    ``MERGED``: ``dst`` is a file; clips are joined in the given order.
    ``SEPARATE``: ``dst`` is an existing folder; one file is written per clip.
    Temporary files are always removed and targets are only replaced on success.
    Returns the written paths. Raises ``TrimmerError`` or ``ExportCancelled``.
    """
    token = token or CancelToken()
    report = on_progress or (lambda _f: None)
    ffmpeg_bin, _ = ensure_ffmpeg()
    info = probe(src)
    merged = output == OutputKind.MERGED
    validate_segments(segments, info.duration, allow_overlap=not merged)
    s, d = Path(src).resolve(), Path(dst).resolve()
    if merged:
        folder, targets = d.parent, [d]
        if s == d:
            raise TrimmerError("Output file must differ from the source file.")
        if d.suffix.lower() not in SUPPORTED_OUT:
            raise TrimmerError(
                f"Unsupported output format. Use one of: {', '.join(SUPPORTED_OUT)}"
            )
    else:
        folder = d
        if not d.is_dir():
            raise TrimmerError(f"Output folder does not exist: {d}")
        targets = [p.resolve() for p in separate_paths(src, str(d), len(segments))]
    if not folder.is_dir() or not os.access(folder, os.W_OK):
        raise TrimmerError(f"No write permission for folder: {folder}")
    for t in targets:
        if t.exists() and not os.access(t, os.W_OK):
            raise TrimmerError(f"No write permission for existing file: {t}")

    total = sum(seg.length for seg in segments)
    try:
        work = Path(tempfile.mkdtemp(prefix=".vt_", dir=folder))
    except OSError as e:
        raise TrimmerError(
            f"Cannot create a temporary file in '{folder}': {e.strerror or e}"
        ) from e
    try:
        report(0.0)
        outputs: list[tuple[Path, Path]] = []
        if merged:
            final = work / f"out{d.suffix.lower()}"
            if mode == Mode.ACCURATE:
                _run(
                    _accurate(str(s), segments, info, str(final)),
                    ffmpeg_bin,
                    total,
                    report,
                    token,
                )
            else:
                _fast(
                    str(s),
                    segments,
                    info,
                    work,
                    final,
                    ffmpeg_bin,
                    total,
                    report,
                    token,
                )
            outputs.append((final, d))
        else:
            done = 0.0
            for i, (seg, target) in enumerate(zip(segments, targets)):
                token.check()
                part = work / f"clip{i:04d}{target.suffix}"

                def cb(f: float, base: float = done, seg: Segment = seg) -> None:
                    report((base + f * seg.length) / total)

                stream = (
                    _accurate(str(s), [seg], info, str(part))
                    if mode == Mode.ACCURATE
                    else _copy_part(str(s), seg, info, str(part))
                )
                _run(stream, ffmpeg_bin, seg.length, cb, token)
                outputs.append((part, target))
                done += seg.length
        token.check()
        for tmp, final_path in outputs:
            os.replace(tmp, final_path)
        report(1.0)
        return [str(t) for _, t in outputs]
    except OSError as e:
        raise TrimmerError(f"Could not write '{dst}': {e.strerror or e}") from e
    finally:
        shutil.rmtree(work, ignore_errors=True)
