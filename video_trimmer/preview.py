"""Video preview: exact ffmpeg-rendered frames, optional QtMultimedia playback, draggable range timeline."""

from __future__ import annotations

import time
from typing import Iterable, Optional

from PyQt6.QtCore import (
    QObject,
    QRectF,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from . import core
from .timeparse import format_time

try:  # the multimedia backend is optional (missing on some Linux installs / minimal envs)
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QVideoWidget

    HAS_MEDIA = True
except ImportError:  # pragma: no cover - depends on the platform
    HAS_MEDIA = False

OK_COLOR, BAD_COLOR = QColor(92, 124, 250, 150), QColor(224, 49, 49, 170)
CUT_COLOR = QColor(255, 146, 43, 190)
HANDLE_COLOR, HANDLE_OFF = QColor("#ffd43b"), QColor("#6b6d80")
SEEK_GUARD = (
    0.3  # seconds during which player position reports are ignored after a manual seek
)
DEBOUNCE_MS = 120  # delay before a scrub position is rendered by FFmpeg
STEP_SECONDS = 1.0


class Timeline(QWidget):
    """Scrubber with padded edges, segment bars and draggable Start ``[`` / End ``]`` handles."""

    seekRequested = pyqtSignal(float)
    handleDragged = pyqtSignal(str, float)  # ("start" | "end", seconds)
    PAD = 10
    GRAB = 9

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(48)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(
            "Click or drag to scrub. Drag the yellow [ ] handles to change the selected segment."
        )
        self._duration = 0.0
        self._position = 0.0
        self._segments: list[tuple[float, float, bool]] = []
        self._removing = False
        self._selection: Optional[tuple[Optional[float], Optional[float]]] = None
        self._handles_enabled = True
        self._drag: Optional[str] = None

    # ------------------------------------------------------------ state
    def set_duration(self, seconds: float) -> None:
        self._duration = max(seconds, 0.0)
        self.update()

    def set_position(self, seconds: float) -> None:
        self._position = seconds
        self.update()

    def set_segments(
        self, segments: Iterable[tuple[float, float, bool]], removing: bool = False
    ) -> None:
        self._segments = list(segments)
        self._removing = removing
        self.update()

    def set_selection(self, start: Optional[float], end: Optional[float]) -> None:
        self._selection = None if start is None and end is None else (start, end)
        self.update()

    def set_handles_enabled(self, enabled: bool) -> None:
        self._handles_enabled = enabled
        self.update()

    # ------------------------------------------------------------ geometry
    def _track_width(self) -> float:
        return max(self.width() - 2 * self.PAD, 1)

    def time_at(self, x: float) -> float:
        if self._duration <= 0:
            return 0.0
        return min(max((x - self.PAD) / self._track_width(), 0.0), 1.0) * self._duration

    def x_at(self, seconds: float) -> float:
        if self._duration <= 0:
            return float(self.PAD)
        return (
            self.PAD
            + min(max(seconds / self._duration, 0.0), 1.0) * self._track_width()
        )

    def handle_at(self, x: float) -> Optional[str]:
        """Return the handle under ``x`` ("start"/"end") or ``None``."""
        if not self._handles_enabled or not self._selection or self._duration <= 0:
            return None
        hits = []
        for name, t in zip(("start", "end"), self._selection):
            if t is not None:
                hx = self.x_at(t)
                if abs(x - hx) <= self.GRAB:
                    hits.append((abs(x - hx), name, hx))
        if not hits:
            return None
        if (
            len(hits) == 2 and abs(hits[0][0] - hits[1][0]) < 0.5
        ):  # stacked handles: pick by side
            return "start" if x < hits[0][2] else "end"
        return min(hits)[1]

    # ------------------------------------------------------------ mouse
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._duration <= 0:
            return
        x = event.position().x()
        self._drag = self.handle_at(x) or "playhead"
        if self._drag == "playhead":
            self.seekRequested.emit(self.time_at(x))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        x = event.position().x()
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag:
            if self._drag == "playhead":
                self.seekRequested.emit(self.time_at(x))
            else:
                self.handleDragged.emit(self._drag, self.time_at(x))
        else:
            self.setCursor(
                Qt.CursorShape.SizeHorCursor
                if self.handle_at(x)
                else Qt.CursorShape.PointingHandCursor
            )

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802
        self._drag = None

    # ------------------------------------------------------------ painting
    def _bracket(self, p: QPainter, x: float, inward: int, color: QColor) -> None:
        p.setPen(QPen(color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(int(x), 7, int(x), 41)
        p.drawLine(int(x), 7, int(x + 7 * inward), 7)
        p.drawLine(int(x), 41, int(x + 7 * inward), 41)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        tw = self._track_width()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#272833"))
        p.drawRoundedRect(QRectF(self.PAD, 16, tw, 16), 6, 6)
        for start, end, ok in self._segments:
            x0, x1 = self.x_at(start), self.x_at(end)
            if x1 - x0 >= 1:
                p.setBrush(
                    BAD_COLOR if not ok else CUT_COLOR if self._removing else OK_COLOR
                )
                p.drawRoundedRect(QRectF(x0, 16, x1 - x0, 16), 4, 4)
        if self._selection and self._duration > 0:
            start, end = self._selection
            color = HANDLE_COLOR if self._handles_enabled else HANDLE_OFF
            if start is not None and end is not None and end > start:
                tint = QColor(color)
                tint.setAlpha(45)
                p.setBrush(tint)
                p.drawRect(
                    QRectF(self.x_at(start), 12, self.x_at(end) - self.x_at(start), 24)
                )
            if start is not None:
                self._bracket(p, self.x_at(start), 1, color)
            if end is not None:
                self._bracket(p, self.x_at(end), -1, color)
        if self._duration > 0:
            x = self.x_at(self._position)
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.drawLine(int(x), 2, int(x), 46)
        p.end()


class FrameView(QLabel):
    """Shows a rendered frame scaled to fit while keeping its aspect ratio."""

    def __init__(self) -> None:
        super().__init__(objectName="frame")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(200, 160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._image: Optional[QImage] = None

    @property
    def has_image(self) -> bool:
        return self._image is not None and not self._image.isNull()

    def set_image(self, image: Optional[QImage]) -> None:
        self._image = image
        if self.has_image:
            self._rescale()
        else:
            self.clear()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.has_image:
            self._rescale()

    def _rescale(self) -> None:
        self.setPixmap(
            QPixmap.fromImage(self._image).scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class _FrameJob(QRunnable):
    def __init__(
        self,
        owner: "FrameRenderer",
        job_id: int,
        src: str,
        seconds: float,
        ffmpeg_bin: Optional[str],
        token: core.CancelToken,
    ) -> None:
        super().__init__()
        self.owner, self.job_id, self.src, self.seconds = owner, job_id, src, seconds
        self.ffmpeg_bin, self.token = ffmpeg_bin, token

    def run(self) -> None:
        try:
            data = core.render_frame(
                self.src, self.seconds, self.ffmpeg_bin, token=self.token
            )
            image = QImage.fromData(data, "PNG")
            if image.isNull():
                raise core.TrimmerError("FFmpeg returned an unreadable frame.")
            self.owner.ready.emit(self.job_id, image)
        except core.ExportCancelled:
            pass
        except RuntimeError:  # owner already destroyed (window closed)
            pass
        except Exception as e:  # noqa: BLE001 - surfaced through the failed signal
            try:
                self.owner.failed.emit(self.job_id, str(e))
            except RuntimeError:
                pass


class FrameRenderer(QObject):
    """Debounced, cancellable background renderer of exact preview frames (latest request wins)."""

    ready = pyqtSignal(int, QImage)
    failed = pyqtSignal(int, str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self.dispatch)
        self._request: Optional[tuple[str, float, Optional[str]]] = None
        self._token: Optional[core.CancelToken] = None
        self.latest_id = 0

    def request(
        self,
        src: str,
        seconds: float,
        ffmpeg_bin: Optional[str] = None,
        immediate: bool = False,
    ) -> int:
        self.latest_id += 1
        self._request = (src, seconds, ffmpeg_bin)
        if immediate:
            self._timer.stop()
            self.dispatch()
        else:
            self._timer.start()
        return self.latest_id

    def dispatch(self) -> None:
        if self._request is None:
            return
        self._cancel_running()
        src, seconds, ffmpeg_bin = self._request
        self._request = None
        self._token = core.CancelToken()
        self._pool.start(
            _FrameJob(self, self.latest_id, src, seconds, ffmpeg_bin, self._token)
        )

    def _cancel_running(self) -> None:
        if self._token:
            self._token.cancel()

    def cancel(self) -> None:
        self._timer.stop()
        self._request = None
        self._cancel_running()
        self.latest_id += 1  # invalidate anything still in flight

    def shutdown(self) -> None:
        self.cancel()
        self._pool.waitForDone(3000)


class PreviewPanel(QWidget):
    """Frame preview, optional playback, scrubber with range handles and Set Start / Set End."""

    setStartRequested = pyqtSignal(float)
    setEndRequested = pyqtSignal(float)
    selectionDragged = pyqtSignal(str, float)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.player = None
        self._pos = 0.0  # the authoritative playhead
        self._fps = 30.0
        self._duration = 0.0
        self._src: Optional[str] = None
        self._ffmpeg: Optional[str] = None
        self._loaded = False
        self._frames_ok = False
        self._playable = False
        self._playing = False
        self._editing = True
        self._guard_until = 0.0
        self._frame_error = ""
        self._play_error = ""
        self._reason = "Preview is unavailable (no FFmpeg and no multimedia backend). You can still type times."

        self.placeholder = QLabel(
            "Load a video to preview it.", objectName="placeholder"
        )
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.placeholder.setMinimumHeight(160)
        self.frame_view = FrameView()
        self.stack = QStackedLayout()
        self.stack.addWidget(self.placeholder)
        self.stack.addWidget(self.frame_view)
        self.video = None
        if HAS_MEDIA:
            try:
                self.video = QVideoWidget()
                self.video.setMinimumHeight(160)
                self.video.setSizePolicy(
                    QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
                )
                self.player = QMediaPlayer(self)
                try:
                    self._audio = QAudioOutput(self)
                    self.player.setAudioOutput(self._audio)
                except Exception:  # noqa: BLE001 - no audio device: playback stays silent
                    self._audio = None
                self.player.setVideoOutput(self.video)
                self.stack.addWidget(self.video)
                self.player.positionChanged.connect(self._on_position)
                self.player.playbackStateChanged.connect(self._on_state)
                self.player.mediaStatusChanged.connect(self._on_media_status)
                self.player.errorOccurred.connect(self._on_error)
            except Exception:  # noqa: BLE001 - backend failed to initialise; frames still work
                self.player, self.video = None, None

        self.renderer = FrameRenderer(self)
        self.renderer.ready.connect(self._on_frame)
        self.renderer.failed.connect(self._on_frame_failed)

        self.timeline = Timeline()
        self.timeline.seekRequested.connect(self.seek)
        self.timeline.handleDragged.connect(self._on_handle_dragged)

        def button(text: str, tip: str, slot) -> QPushButton:
            b = QPushButton(text, clicked=slot)
            b.setToolTip(tip)
            return b

        self.play_btn = button("\u25b6 Play", "Play / pause (Space)", self.toggle_play)
        self.back_btn = button(
            "\u25c0\u25c0 1s",
            "Back 1 second (Ctrl+Left)",
            lambda: self.step(-STEP_SECONDS),
        )
        self.fback_btn = button(
            "\u25c0 Frame", "Back 1 frame (Alt+Left)", lambda: self.step_frames(-1)
        )
        self.ffwd_btn = button(
            "Frame \u25b6", "Forward 1 frame (Alt+Right)", lambda: self.step_frames(1)
        )
        self.fwd_btn = button(
            "1s \u25b6\u25b6",
            "Forward 1 second (Ctrl+Right)",
            lambda: self.step(STEP_SECONDS),
        )
        self.time_label = QLabel("00:00:00.000 / 00:00:00.000", objectName="muted")
        self.set_start_btn = button(
            "Set Start  [",
            "Use the playhead as the start of the selected segment ([)",
            lambda: self.setStartRequested.emit(self.position()),
        )
        self.set_end_btn = button(
            "Set End  ]",
            "Use the playhead as the end of the selected segment (])",
            lambda: self.setEndRequested.emit(self.position()),
        )

        controls = QHBoxLayout()
        for w in (
            self.play_btn,
            self.back_btn,
            self.fback_btn,
            self.ffwd_btn,
            self.fwd_btn,
            self.time_label,
        ):
            controls.addWidget(w)
        controls.addStretch()
        controls.addWidget(self.set_start_btn)
        controls.addWidget(self.set_end_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(self.stack, 1)
        layout.addWidget(self.timeline)
        layout.addLayout(controls)
        self._refresh()

    # ------------------------------------------------------------ state
    @property
    def available(self) -> bool:
        """True when audio/video playback is possible."""
        return self.player is not None

    @property
    def scrub_enabled(self) -> bool:
        return self._loaded and (self._frames_ok or self._playable)

    controls_enabled = scrub_enabled

    @property
    def play_enabled(self) -> bool:
        return self._loaded and self.player is not None and self._playable

    def can_set(self) -> bool:
        return self.scrub_enabled and self._editing

    def position(self) -> float:
        """The playhead. Never queries the media player."""
        return self._pos

    def _refresh(self) -> None:
        scrub = self.scrub_enabled
        for w in (self.back_btn, self.fback_btn, self.ffwd_btn, self.fwd_btn):
            w.setEnabled(scrub)
        self.play_btn.setEnabled(self.play_enabled)
        for w in (self.set_start_btn, self.set_end_btn):
            w.setEnabled(self.can_set())
        self.timeline.setEnabled(scrub)
        self.timeline.set_handles_enabled(self._editing)
        self._update_ui()

    def _update_ui(self) -> None:
        self.timeline.set_position(self._pos)
        self.time_label.setText(
            f"{format_time(self._pos)} / {format_time(self._duration)}"
        )

    def set_editing(self, enabled: bool) -> None:
        self._editing = enabled
        self._refresh()

    def set_segments(
        self, segments: Iterable[tuple[float, float, bool]], removing: bool = False
    ) -> None:
        self.timeline.set_segments(segments, removing)

    def set_selection(self, start: Optional[float], end: Optional[float]) -> None:
        self.timeline.set_selection(start, end)

    # ------------------------------------------------------------ display
    def _show_current(self) -> None:
        """Choose what to display while not playing."""
        if self.frame_view.has_image:
            self.stack.setCurrentWidget(self.frame_view)
        elif self._playable and self.video is not None:
            self.stack.setCurrentWidget(self.video)
        else:
            self.stack.setCurrentWidget(self.placeholder)

    def _request_frame(self, immediate: bool = False) -> None:
        if self._frames_ok and self._src and not self._playing:
            self.renderer.request(self._src, self._pos, self._ffmpeg, immediate)

    # ------------------------------------------------------------ loading
    def load(self, path: str, duration: float, fps: Optional[float] = None) -> None:
        self.renderer.cancel()
        self._frame_error = self._play_error = ""
        self._src, self._duration = path, duration or 0.0
        self._fps = fps if fps and fps > 0 else 30.0
        self._pos, self._loaded, self._playing = 0.0, True, False
        self.frame_view.set_image(None)
        self.timeline.set_duration(self._duration)
        try:
            self._ffmpeg = core.ensure_ffmpeg()[0]
            self._frames_ok = True
        except core.TrimmerError:
            self._ffmpeg, self._frames_ok = None, False
        self._playable = self.player is not None
        if self.player:
            self.player.setSource(QUrl.fromLocalFile(path))
            self.player.pause()
        self.placeholder.setText(
            "Loading preview\u2026"
            if self._frames_ok or self._playable
            else self._reason
        )
        self._show_current()
        self._request_frame(immediate=True)
        self._refresh()

    def clear(self) -> None:
        self.renderer.cancel()
        self._frame_error = self._play_error = ""
        self._loaded = self._frames_ok = self._playable = self._playing = False
        self._pos, self._duration, self._src = 0.0, 0.0, None
        self.frame_view.set_image(None)
        self.timeline.set_duration(0.0)
        if self.player:
            self.player.stop()
            self.player.setSource(QUrl())
        self.placeholder.setText("Load a video to preview it.")
        self.stack.setCurrentWidget(self.placeholder)
        self._refresh()

    def shutdown(self) -> None:
        self.renderer.shutdown()
        if self.player:
            self.player.stop()
            self.player.setSource(QUrl())

    # ------------------------------------------------------------ playhead
    def seek(self, seconds: float) -> None:
        """Move the playhead. ``_pos`` is updated immediately and is never overwritten by the player."""
        if not self.scrub_enabled:
            return
        limit = self._duration if self._duration > 0 else float("inf")
        self._pos = min(max(seconds, 0.0), limit)
        self._guard_until = time.monotonic() + SEEK_GUARD
        if self.player:
            self.player.setPosition(int(self._pos * 1000))
        self._update_ui()
        self._request_frame()

    def step(self, delta: float) -> None:
        self._pause()
        self.seek(self._pos + delta)

    def step_frames(self, count: int) -> None:
        """Move by whole frames, snapping the playhead to the frame grid."""
        self._pause()
        self.seek((round(self._pos * self._fps) + count) / self._fps)

    def _pause(self) -> None:
        if self._playing and self.player:
            self.player.pause()
            self._playing = False

    def toggle_play(self) -> None:
        if not self.play_enabled:
            return
        if self._playing:
            self.player.pause()
            return
        if self._duration and self._pos >= self._duration - 0.05:
            self._pos = 0.0
        self.player.setPosition(int(self._pos * 1000))
        self._guard_until = time.monotonic() + SEEK_GUARD
        self.stack.setCurrentWidget(self.video)
        self.player.play()

    # ------------------------------------------------------------ slots
    def _on_handle_dragged(self, which: str, seconds: float) -> None:
        self.seek(seconds)  # scrub to the exact handle time
        self.selectionDragged.emit(which, seconds)

    def _on_position(self, ms: int) -> None:
        if not self._playing or time.monotonic() < self._guard_until:
            return
        limit = self._duration if self._duration > 0 else float("inf")
        self._pos = min(ms / 1000.0, limit)
        self._update_ui()

    def _on_state(self, state) -> None:
        self._playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setText("\u23f8 Pause" if self._playing else "\u25b6 Play")
        if self._playing:
            self.stack.setCurrentWidget(self.video)
        else:
            self._show_current()
            self._request_frame()

    def _on_media_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self._duration:
            self._pos = self._duration
            self._update_ui()

    def _on_error(self, _error, message: str) -> None:
        self._playable = False
        self._play_error = message or "unsupported codec"
        self._update_placeholder()
        self._show_current()
        self._refresh()

    def _update_placeholder(self) -> None:
        """Combine frame and playback errors so neither hides the other (they arrive in any order)."""
        parts = []
        if self._frame_error:
            parts.append(
                f"The preview frame could not be rendered.\n{self._frame_error}"
            )
        if self._play_error:
            parts.append(
                f"Playback isn't available for this file ({self._play_error}).\n"
                "You can still scrub and enter times manually; export is unaffected."
            )
        if parts:
            self.placeholder.setText("\n\n".join(parts))

    def _on_frame(self, job_id: int, image: QImage) -> None:
        if job_id != self.renderer.latest_id:
            return  # a newer position was requested meanwhile
        self.frame_view.set_image(image)
        if not self._playing:
            self.stack.setCurrentWidget(self.frame_view)

    def _on_frame_failed(self, job_id: int, message: str) -> None:
        if job_id != self.renderer.latest_id or self.frame_view.has_image:
            return
        self._frame_error = message
        self._update_placeholder()
        self._show_current()
