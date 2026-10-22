from unittest import mock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

import time  # noqa: E402

from PyQt6.QtCore import QBuffer, QByteArray, QEvent, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QImage, QMouseEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from video_trimmer import core, gui, preview  # noqa: E402

YES, NO = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No


def _png() -> bytes:
    image = QImage(16, 9, QImage.Format.Format_RGB32)
    image.fill(0x336699)
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    return bytes(data)


PNG = _png()


@pytest.fixture(autouse=True)
def fake_frames(monkeypatch):
    """Never spawn FFmpeg from GUI tests."""
    monkeypatch.setattr(core, "render_frame", lambda *a, **k: PNG)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


INFO = core.MediaInfo(60.0, True, 640, 360, "h264", 30.0)


def _window(keep: bool):
    with mock.patch.object(core, "ensure_ffmpeg", return_value=("ffmpeg", "ffprobe")), \
            mock.patch.object(core, "probe", return_value=INFO):
        w = gui.MainWindow()
        if keep:
            w.action_box.setCurrentIndex(1)
        w._load("/fake/video.mp4")
    return w


@pytest.fixture
def win(app):
    """Window in 'keep only these segments' mode (table starts with the whole video)."""
    w = _window(keep=True)
    yield w
    w.close()


@pytest.fixture
def fresh(app):
    """Window exactly as a new user sees it: default 'cut out' mode."""
    w = _window(keep=False)
    yield w
    w.close()


def _text(w, r, c):
    return w.table.item(r, c).text()


def _fill(w, *rows):
    """Replace all rows with (start, end) text pairs."""
    w.table.setRowCount(0)
    for start, end in rows:
        w._add_row()
        r = w.table.rowCount() - 1
        w.table.item(r, 0).setText(start)
        w.table.item(r, 1).setText(end)


# ---------------------------------------------------------------- table & validation
def test_initial_state(win):
    assert win.table.rowCount() == 1
    assert _text(win, 0, 0) == "00:00:00.000" and _text(win, 0, 1) == "00:01:00.000"
    assert win._valid and win.export_btn.isEnabled()
    assert win.mode_box.currentData() == core.Mode.ACCURATE
    assert win.output_box.currentData() == core.OutputKind.MERGED
    assert win._plan == [core.Segment(0, 60)]
    assert "30 fps" in win.meta_label.text()


@pytest.mark.parametrize("typed,normalized", [
    ("90", "00:01:30.000"), ("1:30", "00:01:30.000"), ("0:1:30", "00:01:30.000"),
    ("45.5", "00:00:45.500"), ("1:05.25", "00:01:05.250"), ("00:00:01,5", "00:00:01.500"),
])
def test_flexible_time_input_is_normalized(win, typed, normalized):
    win.table.item(0, 1).setText(typed)
    assert _text(win, 0, 1) == normalized


def test_live_length_and_total(win):
    _fill(win, ("10", "25"), ("30", "1:00"))
    assert _text(win, 0, gui.COL_LEN) == "00:00:15.000" and _text(win, 1, gui.COL_LEN) == "00:00:30.000"
    assert "2 segments" in win.summary.text() and "00:00:45.000" in win.summary.text()


def test_out_of_bounds_then_recovery(win):
    win.table.item(0, 1).setText("90")
    assert not win._valid and "after the video length" in _text(win, 0, gui.COL_STATUS)
    assert _text(win, 0, gui.COL_STATUS).startswith("\u2715")
    win.table.item(0, 1).setText("45")
    assert win._valid and _text(win, 0, gui.COL_STATUS) == "\u2713 OK"


def test_invalid_overlap_and_empty_cells(win):
    _fill(win, ("0", "30"), ("10", "50"))
    assert not win._valid and not win.export_btn.isEnabled()
    assert "Overlaps segment 1" in _text(win, 1, gui.COL_STATUS)
    win.table.item(1, 0).setText("abc")
    assert "not a valid time" in _text(win, 1, gui.COL_STATUS)
    win.table.item(1, 0).setText("")
    assert "empty" in _text(win, 1, gui.COL_STATUS)
    win.table.item(1, 0).setText("50")
    assert "End must be after start" in _text(win, 1, gui.COL_STATUS)
    win.table.selectRow(1)
    win._remove_rows()
    assert win._valid and win.export_btn.isEnabled()


def test_separate_mode_allows_overlap(win):
    _fill(win, ("0", "30"), ("10", "50"))
    assert not win._valid
    win.output_box.setCurrentIndex(1)
    assert win._valid and "clips" in win.summary.text()
    win.output_box.setCurrentIndex(0)
    assert not win._valid


def test_reorder_rows(win):
    _fill(win, ("0", "10"), ("20", "30"), ("40", "50"))
    win.table.setCurrentCell(0, 0)
    win._move(-1)  # no-op at the top
    assert _text(win, 0, 0) == "00:00:00.000"
    win._move(1)
    assert [_text(win, r, 0) for r in range(3)] == ["00:00:20.000", "00:00:00.000", "00:00:40.000"]
    assert win.table.currentRow() == 1
    win._move(1)
    win._move(1)  # no-op at the bottom
    assert _text(win, 2, 0) == "00:00:00.000"
    assert win._valid


def test_clear_all_with_confirmation(win):
    _fill(win, ("0", "10"), ("20", "30"))
    with mock.patch.object(gui.QMessageBox, "question", return_value=NO):
        win._clear_rows()
    assert win.table.rowCount() == 2
    with mock.patch.object(gui.QMessageBox, "question", return_value=YES):
        win._clear_rows()
    assert win.table.rowCount() == 0 and not win.export_btn.isEnabled()
    assert "Add at least one" in win.summary.text()


def test_remove_without_selection_uses_current_row(win):
    _fill(win, ("0", "10"), ("20", "30"))
    win.table.clearSelection()
    win.table.setCurrentCell(0, 0)
    win.table.clearSelection()
    win._remove_rows()
    assert win.table.rowCount() == 1


# ---------------------------------------------------------------- preview integration
def test_set_start_end_from_playhead(win):
    win._set_time_from_preview(gui.COL_START, 12.5)
    win._set_time_from_preview(gui.COL_END, 20)
    assert _text(win, 0, 0) == "00:00:12.500" and _text(win, 0, 1) == "00:00:20.000" and win._valid
    win.preview.setStartRequested.emit(5.0)
    assert _text(win, 0, 0) == "00:00:05.000"


def test_set_time_creates_row_when_table_empty(win):
    win.table.setRowCount(0)
    win._set_time_from_preview(gui.COL_END, 9)
    assert win.table.rowCount() == 1 and _text(win, 0, 1) == "00:00:09.000"


def test_bracket_shortcuts_use_playhead(win):
    with mock.patch.object(win.preview, "can_set", return_value=True), \
            mock.patch.object(win.preview, "position", return_value=7.0):
        win._shortcut_set(gui.COL_END)
    assert _text(win, 0, 1) == "00:00:07.000"
    with mock.patch.object(win.preview, "can_set", return_value=False):
        win._shortcut_set(gui.COL_END)
    assert _text(win, 0, 1) == "00:00:07.000"


def test_clicking_a_time_seeks_preview(win):
    with mock.patch.object(win.preview, "seek") as seek:
        win._on_cell_clicked(0, gui.COL_END)
        win._on_cell_clicked(0, gui.COL_LEN)
        win.table.item(0, 0).setText("bad")
        win._on_cell_clicked(0, gui.COL_START)
    seek.assert_called_once_with(60.0)


def test_timeline_shows_segments(win):
    _fill(win, ("0", "10"), ("5", "20"))
    assert win.preview.timeline._segments == [(0.0, 10.0, True), (5.0, 20.0, False)]


# ---------------------------------------------------------------- loading
def test_load_failure_keeps_state(win):
    with mock.patch.object(core, "probe", side_effect=core.TrimmerError("bad file")), \
            mock.patch.object(gui.QMessageBox, "warning") as warn:
        win._load("/fake/other.mp4")
    warn.assert_called_once()
    assert win.src == "/fake/video.mp4"


def test_drop_loads_first_local_file(win):
    from PyQt6.QtCore import QMimeData, QUrl
    mime = QMimeData()
    mime.setUrls([QUrl("https://example.com/x.mp4"), QUrl.fromLocalFile("/fake/dropped.mp4")])
    assert win._dropped_file(mime) == "/fake/dropped.mp4"
    assert win._dropped_file(QMimeData()) is None
    event = mock.Mock()
    event.mimeData.return_value = mime
    with mock.patch.object(win, "_load") as load:
        win.dropEvent(event)
    load.assert_called_once_with("/fake/dropped.mp4")


def test_drop_ignored_while_busy(win):
    win.worker = mock.Mock()
    event = mock.Mock()
    win.dragEnterEvent(event)
    event.ignore.assert_called_once()
    win.worker = None


# ---------------------------------------------------------------- export
def test_export_without_valid_segments_warns(win):
    win._clear_rows()
    with mock.patch.object(gui.QMessageBox, "warning") as warn:
        win._export()
    warn.assert_called_once()


def test_export_missing_ffmpeg_warns(win):
    with mock.patch.object(core, "ensure_ffmpeg", side_effect=core.TrimmerError("FFmpeg not found")), \
            mock.patch.object(gui.QMessageBox, "warning") as warn:
        win._export()
    assert "FFmpeg not found" in warn.call_args.args[2]


def test_export_merged_flow_and_cancel(win):
    with mock.patch.object(gui.QFileDialog, "getSaveFileName", return_value=("/fake/out", "")), \
            mock.patch.object(gui.ExportWorker, "start") as start:
        win._export()
    start.assert_called_once()
    assert win.worker._job[1] == "/fake/out.mp4" and win.worker._job[4] == core.OutputKind.MERGED
    assert win.busy and win.cancel_btn.isEnabled() and not win.table.isEnabled()
    assert not win.preview.set_start_btn.isEnabled() and win.progress.maximum() == 0
    win._on_progress(40)
    assert win.progress.maximum() == 100 and win.progress.value() == 40
    with mock.patch.object(gui.ExportWorker, "cancel") as cancel:
        win._cancel_export()
        win._cancel_export()  # second click is ignored
    cancel.assert_called_once()
    win._on_cancelled()
    win._on_finished()
    assert not win.busy and win.table.isEnabled() and win.export_btn.isEnabled()


def test_export_dialog_cancelled_does_nothing(win):
    with mock.patch.object(gui.QFileDialog, "getSaveFileName", return_value=("", "")), \
            mock.patch.object(gui.ExportWorker, "start") as start:
        win._export()
    start.assert_not_called()
    assert not win.busy


def test_export_separate_flow_with_overwrite_prompt(win, tmp_path):
    _fill(win, ("0", "30"), ("10", "50"))
    win.output_box.setCurrentIndex(1)
    win.mode_box.setCurrentIndex(1)
    (tmp_path / "video_clip001.mp4").write_bytes(b"x")
    with mock.patch.object(gui.QFileDialog, "getExistingDirectory", return_value=str(tmp_path)), \
            mock.patch.object(gui.ExportWorker, "start") as start:
        with mock.patch.object(gui.QMessageBox, "question", return_value=NO):
            win._export()
        start.assert_not_called()
        with mock.patch.object(gui.QMessageBox, "question", return_value=YES):
            win._export()
    start.assert_called_once()
    job = win.worker._job
    assert job[1] == str(tmp_path) and job[3] == core.Mode.FAST and job[4] == core.OutputKind.SEPARATE
    assert len(job[2]) == 2


def test_export_separate_folder_dialog_cancelled(win):
    win.output_box.setCurrentIndex(1)
    with mock.patch.object(gui.QFileDialog, "getExistingDirectory", return_value=""), \
            mock.patch.object(gui.ExportWorker, "start") as start:
        win._export()
    start.assert_not_called()


def test_done_and_failed_dialogs(win):
    with mock.patch.object(gui.QMessageBox, "exec"), mock.patch.object(gui.QDesktopServices, "openUrl"):
        win._on_done(["/out/a.mp4"])
        win._on_done(["/out/a.mp4", "/out/b.mp4"])
    assert "Exported 2 clips" in win.log_view.toPlainText()
    with mock.patch.object(gui.QMessageBox, "warning") as warn:
        win._on_failed("FFmpeg failed:\nboom")
    warn.assert_called_once()


def test_missing_ffmpeg_dialog_on_startup(app):
    with mock.patch.object(core, "ensure_ffmpeg", side_effect=core.TrimmerError("FFmpeg not found")):
        w = gui.MainWindow()
        with mock.patch.object(gui.QMessageBox, "critical") as crit:
            w._check_ffmpeg()
    crit.assert_called_once()
    w.close()


def test_close_while_busy_prompts(win):
    win.worker = mock.Mock(isRunning=mock.Mock(return_value=True))
    event = mock.Mock()
    with mock.patch.object(gui.QMessageBox, "question", return_value=NO):
        win.closeEvent(event)
    event.ignore.assert_called_once()
    event2 = mock.Mock()
    with mock.patch.object(gui.QMessageBox, "question", return_value=YES):
        win.closeEvent(event2)
    win.worker.cancel.assert_called_once()
    event2.accept.assert_called_once()
    win.worker = None


def test_worker_signals(app):
    results, cancelled, done = [], [], []
    w = gui.ExportWorker("a", "b", [], core.Mode.FAST)
    w.failed.connect(results.append)
    w.cancelled.connect(lambda: cancelled.append(1))
    w.succeeded.connect(done.append)
    with mock.patch.object(core, "export", side_effect=core.TrimmerError("nope")):
        w.run()
    with mock.patch.object(core, "export", side_effect=core.ExportCancelled()):
        w.run()
    with mock.patch.object(core, "export", side_effect=RuntimeError("x")):
        w.run()
    with mock.patch.object(core, "export", return_value=["/o.mp4"]):
        w.run()
    assert results[0] == "nope" and "Unexpected error" in results[1]
    assert cancelled == [1] and done == [["/o.mp4"]]
    w._emit(0.5)
    w._emit(0.5)


# ---------------------------------------------------------------- timeline & handles
def _ev(kind, x, buttons=Qt.MouseButton.LeftButton):
    return QMouseEvent(kind, QPointF(x, 10), QPointF(x, 10), Qt.MouseButton.LeftButton, buttons,
                       Qt.KeyboardModifier.NoModifier)


PRESS, MOVE = QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove


def _timeline(selection=(20.0, 60.0)):
    tl = preview.Timeline()
    tl.resize(220, 48)
    tl.set_duration(100)
    if selection:
        tl.set_selection(*selection)
    return tl


def test_timeline_has_edge_padding(app):
    tl = _timeline(None)
    assert tl.x_at(0) == preview.Timeline.PAD == 10 and tl.x_at(100) == 210
    assert tl.time_at(10) == 0 and tl.time_at(210) == 100 and tl.time_at(-50) == 0 and tl.time_at(999) == 100
    tl.set_duration(0)
    assert tl.time_at(50) == 0 and tl.x_at(5) == 10


def test_timeline_scrubs_when_not_on_a_handle(app):
    tl = _timeline()
    seeks = []
    tl.seekRequested.connect(seeks.append)
    tl.mousePressEvent(_ev(PRESS, 110))
    tl.mouseMoveEvent(_ev(MOVE, 160))
    tl.mouseReleaseEvent(_ev(MOVE, 160))
    tl.mouseMoveEvent(_ev(MOVE, 180))  # released: hovering only
    assert seeks == [pytest.approx(50.0), pytest.approx(75.0)]
    tl.set_duration(0)
    tl.mousePressEvent(_ev(PRESS, 110))
    assert len(seeks) == 2  # nothing to scrub without a duration


def test_timeline_drags_start_and_end_handles(app):
    tl = _timeline()  # handles at x=50 and x=130
    drags, seeks = [], []
    tl.handleDragged.connect(lambda w, t: drags.append((w, round(t, 3))))
    tl.seekRequested.connect(seeks.append)
    tl.mousePressEvent(_ev(PRESS, 52))
    tl.mouseMoveEvent(_ev(MOVE, 60))
    tl.mouseReleaseEvent(_ev(MOVE, 60))
    tl.mousePressEvent(_ev(PRESS, 128))
    tl.mouseMoveEvent(_ev(MOVE, 160))
    tl.mouseMoveEvent(_ev(MOVE, 1000))  # clamped to the end of the video
    tl.mouseReleaseEvent(_ev(MOVE, 160))
    assert drags == [("start", 25.0), ("end", 75.0), ("end", 100.0)] and seeks == []


def test_timeline_handle_hit_testing_and_hover_cursor(app):
    tl = _timeline()
    assert tl.handle_at(50) == "start" and tl.handle_at(139) == "end" and tl.handle_at(80) is None
    assert tl.handle_at(41) == "start" and tl.handle_at(40) is None
    tl.set_selection(50, 50)  # stacked handles: the side of the click decides
    assert tl.handle_at(108) == "start" and tl.handle_at(112) == "end"
    tl.set_selection(None, 60)
    assert tl.handle_at(50) is None and tl.handle_at(130) == "end"
    tl.set_selection(None, None)
    assert tl.handle_at(130) is None
    tl.set_selection(20, 60)
    tl.mouseMoveEvent(_ev(MOVE, 50, Qt.MouseButton.NoButton))
    assert tl.cursor().shape() == Qt.CursorShape.SizeHorCursor
    tl.mouseMoveEvent(_ev(MOVE, 80, Qt.MouseButton.NoButton))
    assert tl.cursor().shape() == Qt.CursorShape.PointingHandCursor


def test_timeline_handles_can_be_disabled_and_painted(app):
    tl = _timeline()
    tl.set_handles_enabled(False)
    seeks = []
    tl.seekRequested.connect(seeks.append)
    tl.mousePressEvent(_ev(PRESS, 50))
    assert seeks and tl.handle_at(50) is None
    tl.mousePressEvent(_ev(PRESS, 50, Qt.MouseButton.RightButton))
    for sel in ((20, 60), (None, 60), (20, None), (60, 20)):
        tl.set_selection(*sel)
        tl.set_segments([(0, 30, True), (40, 50, False)], removing=True)
        assert not tl.grab().isNull()


# ---------------------------------------------------------------- preview panel
def _panel(fps=25.0):
    with mock.patch.object(core, "ensure_ffmpeg", return_value=("ffmpeg", "ffprobe")):
        p = preview.PreviewPanel()
        p.load("/fake/v.mp4", 30.0, fps)
    return p


def _wait(cond, ms=4000):
    for _ in range(ms // 20):
        if cond():
            return True
        QTest.qWait(20)
    return cond()


def test_playhead_is_authoritative_and_never_queries_the_player(app):
    p = _panel()
    p.player = mock.Mock()
    p.player.position.side_effect = AssertionError("player.position() must not be used")
    p.seek(10)
    p.step(1)
    p.step_frames(-1)
    assert p.position() == pytest.approx(10.96) and p._pos == p.position()
    p.player.setPosition.assert_called_with(int(p.position() * 1000))
    p.shutdown()


def test_player_position_reports_are_ignored_after_a_manual_seek_and_when_paused(app):
    p = _panel()
    p._playing = True
    p.seek(5)
    p._on_position(9000)  # inside the 300 ms guard
    assert p.position() == 5
    with mock.patch.object(preview.time, "monotonic", return_value=time.monotonic() + 1):
        p._on_position(9000)
        assert p.position() == 9.0
        p._on_position(99000)  # clamped to the duration
        assert p.position() == 30.0
        p._playing = False
        p._on_position(1000)  # paused/scrubbing: ignored
        assert p.position() == 30.0
    p.shutdown()


def test_steps_snap_to_the_frame_grid_and_clamp(app):
    p = _panel(fps=25)
    p.seek(10.01)
    p.step_frames(1)
    assert p.position() == pytest.approx(10.04)
    p.step_frames(-2)
    assert p.position() == pytest.approx(9.96)
    p.step(-1)
    p.step(1)
    p.seek(-5)
    p.step(-1)
    assert p.position() == 0
    p.seek(999)
    p.step_frames(1)
    assert p.position() == 30
    assert "00:00:30.000 / 00:00:30.000" in p.time_label.text() and p.timeline._position == 30
    p.shutdown()


def test_scrubbing_renders_one_debounced_exact_frame(app, monkeypatch):
    calls = []
    monkeypatch.setattr(core, "render_frame", lambda src, s, *a, **k: calls.append(s) or PNG)
    p = _panel()
    assert _wait(lambda: p.frame_view.has_image)  # first frame after load
    calls.clear()
    for t in (3, 4, 5):
        p.seek(t)  # rapid scrubbing within the debounce window
    assert _wait(lambda: calls == [5])
    QTest.qWait(300)
    assert calls == [5] and p.stack.currentWidget() is p.frame_view
    p.shutdown()


def test_stale_frames_are_ignored_and_failures_are_reported(app):
    p = _panel()
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    p.frame_view.set_image(None)
    p._on_frame(p.renderer.latest_id - 1, image)
    assert not p.frame_view.has_image
    p._on_frame(p.renderer.latest_id, image)
    assert p.frame_view.has_image
    p.frame_view.set_image(None)
    p._on_frame_failed(p.renderer.latest_id, "boom")
    assert "boom" in p.placeholder.text()
    p.shutdown()


def test_frame_render_failure_from_worker(app, monkeypatch):
    def fail(*_a, **_k):
        raise core.TrimmerError("no frame here")

    monkeypatch.setattr(core, "render_frame", fail)
    p = _panel()
    assert _wait(lambda: "no frame here" in p.placeholder.text())
    p.shutdown()


def test_frame_view_scales_and_clears(app):
    view = preview.FrameView()
    view.show()
    view.resize(300, 200)
    view.set_image(QImage(640, 360, QImage.Format.Format_RGB32))
    assert view.has_image and view.pixmap().width() <= 300
    view.resize(240, 170)
    QTest.qWait(30)
    assert view.pixmap().width() <= 240 and view.pixmap().height() <= 170
    view.close()
    view.set_image(None)
    assert not view.has_image


def test_scrubbing_works_without_multimedia_when_ffmpeg_exists(app):
    with mock.patch.object(preview, "HAS_MEDIA", False):
        p = _panel()
    assert not p.available and p.scrub_enabled and p.can_set() and not p.play_enabled
    assert not p.play_btn.isEnabled() and p.fwd_btn.isEnabled()
    p.seek(7)
    p.toggle_play()
    assert p.position() == 7
    p.set_editing(False)
    assert not p.set_start_btn.isEnabled() and p.fwd_btn.isEnabled()
    p.shutdown()


def test_everything_is_disabled_without_ffmpeg_and_multimedia(app):
    with mock.patch.object(preview, "HAS_MEDIA", False), \
            mock.patch.object(core, "ensure_ffmpeg", side_effect=core.TrimmerError("missing")):
        p = preview.PreviewPanel()
        p.load("/fake/v.mp4", 30.0)
    assert not p.scrub_enabled and not p.can_set() and "unavailable" in p.placeholder.text()
    p.seek(5)
    assert p.position() == 0
    p.clear()
    p.shutdown()


@pytest.mark.skipif(not preview.HAS_MEDIA, reason="QtMultimedia not available")
def test_playback_states_and_errors(app):
    p = _panel()
    assert p.available and p.play_enabled
    p._on_state(preview.QMediaPlayer.PlaybackState.PlayingState)
    assert "Pause" in p.play_btn.text() and p._playing and p.stack.currentWidget() is p.video
    p._on_state(preview.QMediaPlayer.PlaybackState.PausedState)
    assert "Play" in p.play_btn.text() and not p._playing
    p._on_media_status(preview.QMediaPlayer.MediaStatus.EndOfMedia)
    assert p.position() == 30
    p.toggle_play()  # restarts from the beginning when at the end
    p.player.pause()
    p._on_error(None, "unsupported codec")
    assert not p.play_enabled and p.can_set()  # scrubbing keeps working through FFmpeg frames
    assert "Playback isn't available" in p.placeholder.text()
    p.set_editing(False)
    assert not p.can_set()
    p.clear()
    assert not p.scrub_enabled and p.position() == 0
    p.shutdown()


def test_preview_backend_init_failure_keeps_frame_scrubbing(app):
    if not preview.HAS_MEDIA:
        pytest.skip("QtMultimedia not available")
    with mock.patch.object(preview, "QMediaPlayer", side_effect=RuntimeError("no backend")):
        p = _panel()
    assert not p.available and p.scrub_enabled
    p.shutdown()


# ---------------------------------------------------------------- handles <-> table
def test_selecting_a_row_highlights_its_handles(win):
    _fill(win, ("10", "20"), ("30", "45"))
    win.table.setCurrentCell(0, 0)
    assert win.preview.timeline._selection == (10.0, 20.0)
    win.table.setCurrentCell(1, 1)
    assert win.preview.timeline._selection == (30.0, 45.0)
    win.table.item(1, 0).setText("abc")  # unparsable cell: only the valid handle remains
    assert win.preview.timeline._selection == (None, 45.0)
    win.table.setRowCount(0)
    win._revalidate()
    assert win.preview.timeline._selection is None


def test_dragging_a_handle_updates_the_cell_and_scrubs(win):
    _fill(win, ("10", "40"))
    win.table.setCurrentCell(0, 0)
    tl = win.preview.timeline
    tl.resize(1010, 48)
    tl.mousePressEvent(_ev(PRESS, tl.x_at(10)))
    tl.mouseMoveEvent(_ev(MOVE, tl.x_at(15)))
    assert _text(win, 0, 0) == "00:00:15.000" and win.preview.position() == pytest.approx(15.0)
    assert tl._selection == (15.0, 40.0)
    tl.mouseReleaseEvent(_ev(MOVE, tl.x_at(15)))
    win.preview.selectionDragged.emit("end", 12.3456)
    assert _text(win, 0, 1) == "00:00:12.346" and not win._valid
    assert "End must be after start" in _text(win, 0, gui.COL_STATUS)


def test_handle_drag_is_ignored_when_busy_or_without_a_row(win):
    _fill(win, ("10", "40"))
    win.table.setCurrentCell(0, 0)
    win.worker = mock.Mock()
    win.preview.selectionDragged.emit("start", 5.0)
    assert _text(win, 0, 0) == "00:00:10.000"
    win.worker = None
    win.table.setRowCount(0)
    win.preview.selectionDragged.emit("start", 5.0)
    assert win.table.rowCount() == 0


def test_add_creates_the_segment_at_the_playhead(win, fresh):
    win.preview.seek(30)
    win._add_row()
    assert (_text(win, 1, 0), _text(win, 1, 1)) == ("00:00:30.000", "00:01:00.000")  # keep: playhead -> end
    fresh.preview.seek(12.5)
    fresh._add_row()
    assert (_text(fresh, 0, 0), _text(fresh, 0, 1)) == ("00:00:12.500", "00:00:13.500")
    fresh.preview.seek(60)
    fresh._add_row()
    assert (_text(fresh, 1, 0), _text(fresh, 1, 1)) == ("00:00:59.000", "00:01:00.000")


def test_frame_step_actions_and_shortcuts(win):
    assert win.act_fback.shortcut().toString() == "Alt+Left" and win.act_ffwd.shortcut().toString() == "Alt+Right"
    win.preview.seek(10)
    win.act_ffwd.trigger()
    assert win.preview.position() == pytest.approx(10 + 1 / 30)
    win.act_fback.trigger()
    assert win.preview.position() == pytest.approx(10.0)
    win.act_fwd.trigger()
    assert win.preview.position() == pytest.approx(11.0)
    win.act_back.trigger()
    assert win.preview.position() == pytest.approx(10.0)


def test_editing_is_locked_during_export_including_handles(win):
    win.worker = mock.Mock()
    win._set_busy(True)
    assert not win.preview.timeline._handles_enabled and not win.preview.set_start_btn.isEnabled()
    win.worker = None
    win._set_busy(False)
    assert win.preview.timeline._handles_enabled


# ---------------------------------------------------------------- cut-out (remove) mode
def test_default_mode_is_cut_out_and_starts_empty(fresh):
    assert fresh.removing and fresh.action_box.currentData() == core.Action.REMOVE
    assert fresh.table.rowCount() == 0 and not fresh.export_btn.isEnabled() and fresh.add_btn.isEnabled()
    assert "cut out" in fresh.summary.text()
    assert [fresh.table.horizontalHeaderItem(i).text() for i in range(2)] == ["Cut from", "Cut to"]


def test_add_is_disabled_until_a_video_is_loaded(app):
    with mock.patch.object(core, "ensure_ffmpeg", return_value=("ffmpeg", "ffprobe")):
        w = gui.MainWindow()
    assert not w.add_btn.isEnabled() and not w.export_btn.isEnabled()
    w.close()


def test_cutting_one_second_from_each_end_keeps_the_middle(fresh):
    _fill(fresh, ("0", "1"), ("59", "60"))
    assert fresh._valid and fresh._plan == [core.Segment(1, 59)]
    assert "2 cuts" in fresh.summary.text() and "00:00:58.000 of 00:01:00.000" in fresh.summary.text()
    assert fresh.preview.timeline._removing
    with mock.patch.object(gui.QFileDialog, "getSaveFileName", return_value=("/fake/o.mp4", "")), \
            mock.patch.object(gui.ExportWorker, "start"):
        fresh._export()
    assert fresh.worker._job[2] == [core.Segment(1, 59)]  # the exported part is the REST, not the cuts


def test_cuts_may_overlap_and_are_merged(fresh):
    _fill(fresh, ("10", "30"), ("20", "40"))
    assert fresh._valid and fresh._plan == [core.Segment(0, 10), core.Segment(40, 60)]
    assert "keeps 00:00:30.000" in fresh.summary.text()


def test_cutting_everything_is_rejected(fresh):
    _fill(fresh, ("0", "60"))
    assert not fresh._valid and "nothing would be left" in fresh.summary.text()
    with mock.patch.object(gui.QMessageBox, "warning") as warn:
        fresh._export()
    warn.assert_called_once()


def test_out_of_bounds_cut_is_flagged(fresh):
    _fill(fresh, ("50", "90"))
    assert not fresh._valid and "after the video length" in _text(fresh, 0, gui.COL_STATUS)


def test_cut_mode_needs_a_known_duration(fresh):
    fresh.info = core.MediaInfo(None, True)
    _fill(fresh, ("1", "2"))
    assert not fresh._valid and "duration is unknown" in fresh.summary.text()


def test_cut_mode_separate_files_summary(fresh):
    _fill(fresh, ("10", "20"))
    fresh.output_box.setCurrentIndex(1)
    assert fresh._plan == [core.Segment(0, 10), core.Segment(20, 60)] and "2 files" in fresh.summary.text()


def test_switching_mode_relabels_and_recomputes(fresh):
    _fill(fresh, ("0", "1"))
    assert fresh._plan == [core.Segment(1, 60)]
    fresh.action_box.setCurrentIndex(1)
    assert fresh._plan == [core.Segment(0, 1)]
    assert fresh.table.horizontalHeaderItem(0).text() == "Start" and not fresh.preview.timeline._removing
    assert fresh._native("/a/b") in ("/a/b", "\\a\\b")


def test_cell_editor_is_not_clipped(fresh):
    """Regression: the inline editor used to crop the time text (padding larger than the row)."""
    from video_trimmer.style import STYLE
    fresh.setStyleSheet(STYLE)
    _fill(fresh, ("0", "1"))
    fresh.show()
    fresh.table.editItem(fresh.table.item(0, 1))
    editor = fresh.table.viewport().findChild(gui.QLineEdit)
    assert editor is not None and editor.height() >= editor.fontMetrics().height() + 4
    assert "QTableWidget QLineEdit" in STYLE and "padding: 0 8px" in STYLE
