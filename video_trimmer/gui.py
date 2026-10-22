"""PyQt6 user interface."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QDir, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor, QDesktopServices, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__, core
from .preview import PreviewPanel
from .timeparse import format_time, parse_time

BAD = QBrush(QColor(122, 38, 46))
RED, GREEN = QColor("#ff7b7b"), QColor("#6fdc8c")
COL_START, COL_END, COL_LEN, COL_STATUS = range(4)
VIDEO_FILTER = (
    "Video files (*.mp4 *.mkv *.mov *.avi *.webm *.flv *.wmv *.m4v *.ts);;All files (*)"
)
EDITABLE = (
    Qt.ItemFlag.ItemIsEnabled
    | Qt.ItemFlag.ItemIsSelectable
    | Qt.ItemFlag.ItemIsEditable
)
READONLY = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class ExportWorker(QThread):
    """Runs ``core.export`` off the UI thread."""

    progress = pyqtSignal(int)
    succeeded = pyqtSignal(list)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(
        self,
        src: str,
        dst: str,
        segments: list[core.Segment],
        mode: core.Mode,
        output: core.OutputKind = core.OutputKind.MERGED,
    ) -> None:
        super().__init__()
        self._job = (src, dst, segments, mode, output)
        self.token = core.CancelToken()
        self._last = -1

    def _emit(self, fraction: float) -> None:
        pct = int(fraction * 100)
        if pct != self._last:
            self._last = pct
            self.progress.emit(pct)

    def cancel(self) -> None:
        self.token.cancel()

    def run(self) -> None:
        src, dst, segments, mode, output = self._job
        try:
            paths = core.export(
                src, dst, segments, mode, self._emit, self.token, output
            )
        except core.ExportCancelled:
            self.cancelled.emit()
        except core.TrimmerError as e:
            self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001 - last-resort surfacing to the GUI
            self.failed.emit(f"Unexpected error: {e!r}")
        else:
            self.succeeded.emit(paths)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Video Trimmer")
        self.resize(980, 860)
        self.setAcceptDrops(True)
        self.src: Optional[str] = None
        self.info: Optional[core.MediaInfo] = None
        self.worker: Optional[ExportWorker] = None
        self._updating = False
        self._valid = False
        self._segments: list[Optional[core.Segment]] = []
        self._plan: list[core.Segment] = []  # parts that will actually be exported
        self._build_ui()
        self._build_actions()
        self._on_action_changed()  # sets header labels and validates
        QTimer.singleShot(0, self._check_ffmpeg)

    # ------------------------------------------------------------ construction
    def _build_ui(self) -> None:
        self.drop = QFrame(objectName="drop")
        self.path_edit = QLineEdit(
            readOnly=True,
            placeholderText="Drag & drop a video here, or click Browse\u2026",
        )
        self.path_edit.setToolTip("Path of the loaded video")
        self.browse_btn = QPushButton("Browse\u2026", clicked=self._browse)
        self.browse_btn.setToolTip("Select a video file (Ctrl+O)")
        self.meta_label = QLabel("No file loaded.", objectName="muted")
        row = QHBoxLayout()
        row.addWidget(self.path_edit, 1)
        row.addWidget(self.browse_btn)
        drop_layout = QVBoxLayout(self.drop)
        drop_layout.setContentsMargins(14, 10, 14, 10)
        drop_layout.addLayout(row)
        drop_layout.addWidget(self.meta_label)

        self.preview = PreviewPanel()
        self.preview.setStartRequested.connect(
            lambda s: self._set_time_from_preview(COL_START, s)
        )
        self.preview.setEndRequested.connect(
            lambda s: self._set_time_from_preview(COL_END, s)
        )
        self.preview.selectionDragged.connect(self._on_handle_dragged)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Start", "End", "Length", "Status"])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self.table.setToolTip(
            "Double-click a time to edit: HH:MM:SS, MM:SS or seconds, with optional .ms"
        )
        head = self.table.horizontalHeader()
        for col, mode in (
            (COL_START, QHeaderView.ResizeMode.Stretch),
            (COL_END, QHeaderView.ResizeMode.Stretch),
            (COL_LEN, QHeaderView.ResizeMode.ResizeToContents),
            (COL_STATUS, QHeaderView.ResizeMode.Stretch),
        ):
            head.setSectionResizeMode(col, mode)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.currentCellChanged.connect(lambda *_: self._sync_selection())

        def button(text: str, tip: str, slot) -> QPushButton:
            b = QPushButton(text, clicked=slot)
            b.setToolTip(tip)
            return b

        self.add_btn = button(
            "Add", "Append a new segment (Ctrl+N)", lambda: self._add_row()
        )
        self.del_btn = button(
            "Remove", "Delete the selected segments (Delete)", self._remove_rows
        )
        self.clear_btn = button("Clear All", "Remove every segment", self._clear_rows)
        self.up_btn = button(
            "\u25b2 Up",
            "Move the selected segment up (Ctrl+Up)",
            lambda: self._move(-1),
        )
        self.down_btn = button(
            "\u25bc Down",
            "Move the selected segment down (Ctrl+Down)",
            lambda: self._move(1),
        )
        self.summary = QLabel("", objectName="muted")
        tools = QHBoxLayout()
        for b in (
            self.add_btn,
            self.del_btn,
            self.clear_btn,
            self.up_btn,
            self.down_btn,
        ):
            tools.addWidget(b)
        tools.addSpacing(8)
        tools.addWidget(self.summary, 1)

        segments = QWidget()
        seg_layout = QVBoxLayout(segments)
        seg_layout.setContentsMargins(0, 0, 0, 0)
        seg_layout.setSpacing(8)
        seg_layout.addLayout(tools)
        seg_layout.addWidget(self.table, 1)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.preview)
        splitter.addWidget(segments)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self.action_box = QComboBox()
        self.action_box.addItem("Cut out these segments (remove)", core.Action.REMOVE)
        self.action_box.addItem("Keep only these segments", core.Action.KEEP)
        self.action_box.setItemData(
            0,
            "Listed segments are deleted; the rest of the video is exported.",
            Qt.ItemDataRole.ToolTipRole,
        )
        self.action_box.setItemData(
            1, "Only the listed segments are exported.", Qt.ItemDataRole.ToolTipRole
        )
        self.action_box.setToolTip(
            "Decide whether the listed segments are removed or kept"
        )
        self.action_box.currentIndexChanged.connect(
            lambda _i: self._on_action_changed()
        )
        self.output_box = QComboBox()
        self.output_box.addItem("Merge clips into one video", core.OutputKind.MERGED)
        self.output_box.addItem(
            "Export clips as separate files", core.OutputKind.SEPARATE
        )
        self.output_box.setItemData(
            0,
            "Join all segments, in the listed order, into a single file.",
            Qt.ItemDataRole.ToolTipRole,
        )
        self.output_box.setItemData(
            1,
            "Write one file per segment (segments may overlap).",
            Qt.ItemDataRole.ToolTipRole,
        )
        self.output_box.setToolTip("Choose how segments are exported")
        self.output_box.currentIndexChanged.connect(lambda _i: self._revalidate())
        self.mode_box = QComboBox()
        self.mode_box.addItem("Frame-accurate (re-encode)", core.Mode.ACCURATE)
        self.mode_box.addItem("Fast stream copy", core.Mode.FAST)
        self.mode_box.setItemData(
            0,
            "Exact cut points. Re-encodes to H.264/AAC (slower).",
            Qt.ItemDataRole.ToolTipRole,
        )
        self.mode_box.setItemData(
            1,
            "No re-encoding: very fast and lossless, but cuts snap to keyframes.",
            Qt.ItemDataRole.ToolTipRole,
        )
        self.mode_box.setToolTip("Choose between exact cuts and fast keyframe cuts")

        self.export_btn = QPushButton(
            "Export\u2026", objectName="primary", clicked=self._export
        )
        self.export_btn.setToolTip("Export the segments (Ctrl+E)")
        self.cancel_btn = QPushButton(
            "Cancel Export", objectName="danger", clicked=self._cancel_export
        )
        self.cancel_btn.setToolTip(
            "Stop the running export and discard partial output (Esc)"
        )
        self.cancel_btn.hide()

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.export_btn)
        buttons.addWidget(self.cancel_btn)
        options = QGridLayout()
        options.setHorizontalSpacing(10)
        options.addWidget(QLabel("Segments:", objectName="muted"), 0, 0)
        options.addWidget(self.action_box, 0, 1)
        options.addWidget(QLabel("Output:", objectName="muted"), 0, 2)
        options.addWidget(self.output_box, 0, 3)
        options.addWidget(QLabel("Method:", objectName="muted"), 1, 0)
        options.addWidget(self.mode_box, 1, 1)
        options.addLayout(buttons, 1, 2, 1, 2)
        options.setColumnStretch(1, 1)
        options.setColumnStretch(3, 1)

        self.progress = QProgressBar(maximum=100, visible=False)
        self.log_view = QPlainTextEdit(readOnly=True, maximumBlockCount=500)
        self.log_view.setMaximumHeight(90)
        self.log_view.setPlaceholderText("Activity log")
        self.log_view.setAcceptDrops(False)

        layout = QVBoxLayout()
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(10)
        layout.addWidget(self.drop)
        layout.addWidget(splitter, 1)
        layout.addLayout(options)
        layout.addWidget(self.progress)
        layout.addWidget(self.log_view)
        root = QWidget()
        root.setLayout(layout)
        self.setCentralWidget(root)

    def _build_actions(self) -> None:
        def act(text: str, slot, shortcut=None) -> QAction:
            a = QAction(text, self, triggered=slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            self.addAction(a)
            return a

        self.act_open = act("&Open video\u2026", self._browse, "Ctrl+O")
        self.act_export = act("&Export\u2026", self._export, "Ctrl+E")
        self.act_cancel = act("&Cancel export", self._cancel_export, "Esc")
        self.act_add = act("&Add segment", lambda: self._add_row(), "Ctrl+N")
        self.act_remove = act("&Remove selected", self._remove_rows, "Delete")
        self.act_clear = act("C&lear all", self._clear_rows)
        self.act_up = act("Move &up", lambda: self._move(-1), "Ctrl+Up")
        self.act_down = act("Move &down", lambda: self._move(1), "Ctrl+Down")
        self.act_play = act("&Play / pause", self.preview.toggle_play, "Space")
        self.act_back = act("&Back 1 s", lambda: self.preview.step(-1.0), "Ctrl+Left")
        self.act_fwd = act("&Forward 1 s", lambda: self.preview.step(1.0), "Ctrl+Right")
        self.act_fback = act(
            "Back 1 &frame", lambda: self.preview.step_frames(-1), "Alt+Left"
        )
        self.act_ffwd = act(
            "Forward 1 f&rame", lambda: self.preview.step_frames(1), "Alt+Right"
        )
        self.act_set_start = act(
            "Set &start at playhead", lambda: self._shortcut_set(COL_START), "["
        )
        self.act_set_end = act(
            "Set &end at playhead", lambda: self._shortcut_set(COL_END), "]"
        )
        quit_act = act("&Quit", self.close, "Ctrl+Q")
        about = act("&About", self._about)
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")
        file_menu.addActions([self.act_open, self.act_export, self.act_cancel])
        file_menu.addSeparator()
        file_menu.addAction(quit_act)
        seg_menu = menu.addMenu("&Segments")
        seg_menu.addActions(
            [self.act_add, self.act_remove, self.act_clear, self.act_up, self.act_down]
        )
        play_menu = menu.addMenu("&Playback")
        play_menu.addActions(
            [
                self.act_play,
                self.act_back,
                self.act_fwd,
                self.act_fback,
                self.act_ffwd,
                self.act_set_start,
                self.act_set_end,
            ]
        )
        menu.addMenu("&Help").addAction(about)

    # ------------------------------------------------------------ helpers
    def _log(self, message: str) -> None:
        self.log_view.appendPlainText(f"[{datetime.now():%H:%M:%S}] {message}")

    def _error(
        self, message: str, title: str = "Error", critical: bool = False
    ) -> None:
        self._log(f"ERROR: {message.splitlines()[0]}")
        (QMessageBox.critical if critical else QMessageBox.warning)(
            self, title, message
        )

    @property
    def busy(self) -> bool:
        return self.worker is not None

    @property
    def removing(self) -> bool:
        return self.action_box.currentData() == core.Action.REMOVE

    @property
    def separate(self) -> bool:
        return self.output_box.currentData() == core.OutputKind.SEPARATE

    @property
    def allow_overlap(self) -> bool:
        return self.removing or self.separate

    @staticmethod
    def _native(path: str) -> str:
        return QDir.toNativeSeparators(path)

    def _on_action_changed(self) -> None:
        labels = ["Cut from", "Cut to"] if self.removing else ["Start", "End"]
        self.table.setHorizontalHeaderLabels(labels + ["Length", "Status"])
        self._revalidate()

    def _set_busy(self, busy: bool) -> None:
        for w in (
            self.browse_btn,
            self.add_btn,
            self.del_btn,
            self.clear_btn,
            self.up_btn,
            self.down_btn,
            self.mode_box,
            self.output_box,
            self.action_box,
            self.table,
        ):
            w.setEnabled(not busy)
        for a in (
            self.act_open,
            self.act_add,
            self.act_remove,
            self.act_clear,
            self.act_up,
            self.act_down,
        ):
            a.setEnabled(not busy)
        self.preview.set_editing(not busy)
        self.act_cancel.setEnabled(busy)
        self.cancel_btn.setVisible(busy)
        self.cancel_btn.setEnabled(busy)
        self.cancel_btn.setText("Cancel Export")
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(
                0, 0
            )  # indeterminate until the first progress report
        self._refresh_export_enabled()

    def _refresh_export_enabled(self) -> None:
        ok = bool(self.src) and self._valid and not self.busy
        self.add_btn.setEnabled(bool(self.src) and not self.busy)
        self.act_add.setEnabled(bool(self.src) and not self.busy)
        self.export_btn.setVisible(not self.busy)
        self.export_btn.setEnabled(ok)
        self.act_export.setEnabled(ok)

    def _check_ffmpeg(self) -> None:
        try:
            core.ensure_ffmpeg()
        except core.TrimmerError as e:
            self._error(str(e), "FFmpeg missing", critical=True)

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About Video Trimmer",
            f"<b>Video Trimmer</b> {__version__}<br>Cut and join video segments with FFmpeg.",
        )

    # ------------------------------------------------------------ drag & drop
    @staticmethod
    def _dropped_file(mime) -> Optional[str]:
        for url in mime.urls():
            if url.isLocalFile():
                return url.toLocalFile()
        return None

    def _set_dragging(self, on: bool) -> None:
        self.drop.setProperty("dragging", on)
        self.drop.style().unpolish(self.drop)
        self.drop.style().polish(self.drop)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if not self.busy and self._dropped_file(event.mimeData()):
            event.acceptProposedAction()
            self._set_dragging(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_dragging(False)

    def dropEvent(self, event) -> None:  # noqa: N802
        self._set_dragging(False)
        path = self._dropped_file(event.mimeData())
        if path and not self.busy:
            event.acceptProposedAction()
            self._load(path)

    # ------------------------------------------------------------ file loading
    def _browse(self) -> None:
        start = str(Path(self.src).parent) if self.src else ""
        path, _ = QFileDialog.getOpenFileName(self, "Select video", start, VIDEO_FILTER)
        if path:
            self._load(path)

    def _load(self, path: str) -> None:
        try:
            info = core.probe(path)
        except core.TrimmerError as e:
            self._error(str(e), "Cannot open file")
            return
        self.src, self.info = path, info
        self.path_edit.setText(self._native(path))
        self.meta_label.setText(info.summary())
        self.setWindowTitle(f"Video Trimmer \u2014 {Path(path).name}")
        self._log(f"Loaded {Path(path).name}: {info.summary()}")
        self.preview.load(path, info.duration or 0.0, info.fps)
        if self.table.rowCount() == 0 and not self.removing:
            self._add_row()  # "keep" mode starts with the whole video selected
        else:
            self._revalidate()

    # ------------------------------------------------------------ segment table
    def _make_item(self, text: str, flags) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(flags)
        return item

    def _add_row(self) -> None:
        duration = self.info.duration if self.info else None
        start = self.preview.position()  # new segments begin exactly at the playhead
        if duration and start >= duration:
            start = max(duration - 1.0, 0.0)
        if self.removing:  # default cut: one second
            end_text = format_time(
                min(start + 1.0, duration) if duration else start + 1.0
            )
        else:  # default keep: from the playhead to the end
            end_text = format_time(duration) if duration and duration > start else ""
        self._updating = True
        try:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(
                row, COL_START, self._make_item(format_time(start), EDITABLE)
            )
            self.table.setItem(row, COL_END, self._make_item(end_text, EDITABLE))
            self.table.setItem(row, COL_LEN, self._make_item("", READONLY))
            self.table.setItem(row, COL_STATUS, self._make_item("", READONLY))
        finally:
            self._updating = False
        self._revalidate()
        self.table.setCurrentCell(row, COL_START)
        self.table.selectRow(row)

    def _selected_rows(self) -> list[int]:
        rows = {i.row() for i in self.table.selectedIndexes()}
        if not rows and self.table.currentRow() >= 0:
            rows = {self.table.currentRow()}
        return sorted(rows, reverse=True)

    def _remove_rows(self) -> None:
        for r in self._selected_rows():
            self.table.removeRow(r)
        self._revalidate()

    def _clear_rows(self) -> None:
        count = self.table.rowCount()
        if (
            count > 1
            and QMessageBox.question(
                self, "Clear all segments", f"Remove all {count} segments?"
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.table.setRowCount(0)
        self._revalidate()

    def _move(self, delta: int) -> None:
        row = self.table.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self.table.rowCount():
            return
        self._updating = True
        try:
            for c in (COL_START, COL_END):
                a, b = self.table.item(row, c), self.table.item(target, c)
                a_text, b_text = a.text(), b.text()
                a.setText(b_text)
                b.setText(a_text)
        finally:
            self._updating = False
        col = max(self.table.currentColumn(), COL_START)
        self.table.setCurrentCell(target, col)
        self.table.selectRow(target)
        self._revalidate()

    def _target_row(self) -> int:
        row = self.table.currentRow()
        if row < 0:
            row = self.table.rowCount() - 1
        if row < 0:
            self._add_row()
            row = 0
        return row

    def _set_time_from_preview(self, col: int, seconds: float) -> None:
        if self.busy:
            return
        row = self._target_row()
        self.table.item(row, col).setText(format_time(seconds))
        self.table.setCurrentCell(row, col)
        self.table.selectRow(row)

    def _shortcut_set(self, col: int) -> None:
        if self.preview.can_set():
            self._set_time_from_preview(col, self.preview.position())

    def _row_times(self, row: int) -> tuple[Optional[float], Optional[float]]:
        out: list[Optional[float]] = []
        for col in (COL_START, COL_END):
            item = (
                self.table.item(row, col) if 0 <= row < self.table.rowCount() else None
            )
            try:
                out.append(parse_time(item.text()) if item else None)
            except ValueError:
                out.append(None)
        return out[0], out[1]

    def _sync_selection(self) -> None:
        """Show the selected row's Start/End handles on the timeline."""
        row = self.table.currentRow()
        self.preview.set_selection(
            *(self._row_times(row) if row >= 0 else (None, None))
        )

    def _on_handle_dragged(self, which: str, seconds: float) -> None:
        row = self.table.currentRow()
        if self.busy or row < 0:
            return
        item = self.table.item(row, COL_START if which == "start" else COL_END)
        if item is not None:
            item.setText(format_time(seconds))  # normalises and revalidates live

    def _on_cell_clicked(self, row: int, col: int) -> None:
        if col in (COL_START, COL_END) and self.table.item(row, col):
            try:
                self.preview.seek(parse_time(self.table.item(row, col).text()))
            except ValueError:
                pass

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating:
            return
        if item.column() in (COL_START, COL_END):
            self._updating = True
            try:
                item.setText(format_time(parse_time(item.text())))
            except ValueError:
                pass  # left as typed; highlighted by validation below
            finally:
                self._updating = False
        self._revalidate()

    def _revalidate(self) -> None:
        """Parse every row, highlight problems in real time and update the Export button."""
        rows = self.table.rowCount()
        parsed: list[Optional[core.Segment]] = []
        cell_errors: dict[tuple[int, int], str] = {}
        for r in range(rows):
            vals: list[Optional[float]] = []
            for c in (COL_START, COL_END):
                item = self.table.item(r, c)
                try:
                    vals.append(parse_time(item.text() if item else ""))
                except ValueError as e:
                    vals.append(None)
                    cell_errors[(r, c)] = str(e)
            parsed.append(core.Segment(vals[0], vals[1]) if None not in vals else None)
        duration = self.info.duration if self.info else None
        issues = core.find_issues(parsed, duration, self.allow_overlap)

        self._updating = True
        try:
            for r in range(rows):
                bad_cols = {c for (rr, c) in cell_errors if rr == r}
                msg = next(
                    (m for (rr, _c), m in cell_errors.items() if rr == r), issues.get(r)
                )
                if r in issues and not bad_cols:
                    bad_cols = {COL_START, COL_END}
                for c in (COL_START, COL_END):
                    item = self.table.item(r, c)
                    if item is None:
                        continue
                    item.setBackground(BAD if c in bad_cols else QBrush())
                    item.setToolTip(msg if c in bad_cols and msg else "")
                seg = parsed[r]
                self.table.item(r, COL_LEN).setText(
                    format_time(seg.length) if seg and seg.length > 0 else "\u2014"
                )
                status = self.table.item(r, COL_STATUS)
                status.setText(f"\u2715 {msg}" if msg else "\u2713 OK")
                status.setForeground(RED if msg else GREEN)
                status.setToolTip(msg or "")
        finally:
            self._updating = False

        self._segments = parsed
        self.preview.set_segments(
            [
                (s.start, s.end, i not in issues)
                for i, s in enumerate(parsed)
                if s and s.length > 0
            ],
            self.removing,
        )
        valid_rows = rows > 0 and not cell_errors and not issues
        plan: list[core.Segment] = []
        problem = ""
        if valid_rows:
            segs = [s for s in parsed if s is not None]
            if self.removing:
                try:
                    plan = core.invert_segments(segs, duration)
                except core.TrimmerError as e:
                    problem = str(e)
                else:
                    if not plan:
                        problem = (
                            "The cuts cover the whole video, so nothing would be left."
                        )
            else:
                plan = segs
        self._plan = plan
        self._valid = valid_rows and not problem
        kept, plural = sum(s.length for s in plan), "s" if rows != 1 else ""
        if rows == 0:
            text, name = (
                (
                    "Add the parts you want to cut out."
                    if self.removing
                    else "Add at least one segment."
                ),
                "muted",
            )
        elif problem:
            text, name = problem, "error"
        elif not valid_rows:
            text, name = "Fix the highlighted segments to enable export.", "error"
        elif self.removing:
            files = (
                f"  \u2022  {len(plan)} file{'s' if len(plan) != 1 else ''}"
                if self.separate
                else ""
            )
            text, name = (
                f"{rows} cut{plural}  \u2022  keeps {format_time(kept)} of {format_time(duration)}{files}",
                "ok",
            )
        else:
            text, name = (
                f"{rows} {'clip' if self.separate else 'segment'}{plural}  \u2022  total {format_time(kept)}",
                "ok",
            )
        self.summary.setText(text)
        self.summary.setObjectName(name)
        self.summary.style().unpolish(self.summary)
        self.summary.style().polish(self.summary)
        self._refresh_export_enabled()
        self._sync_selection()

    # ------------------------------------------------------------ export
    def _choose_destination(self, output: core.OutputKind, count: int) -> Optional[str]:
        src = Path(self.src)
        if output == core.OutputKind.MERGED:
            suffix = core.default_suffix(self.src)
            dst, _ = QFileDialog.getSaveFileName(
                self,
                "Export as",
                str(src.with_name(f"{src.stem}_trimmed{suffix}")),
                "Video (*.mp4 *.mkv *.mov)",
            )
            if dst and not Path(dst).suffix:
                dst += suffix
            return dst or None
        folder = QFileDialog.getExistingDirectory(
            self, "Choose output folder", str(src.parent)
        )
        if not folder:
            return None
        clashes = [
            p.name for p in core.separate_paths(self.src, folder, count) if p.exists()
        ]
        if (
            clashes
            and QMessageBox.question(
                self,
                "Overwrite files?",
                f"{len(clashes)} file(s) already exist (e.g. {clashes[0]}). Overwrite them?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return None
        return folder

    def _export(self) -> None:
        if self.busy:
            return
        try:
            core.ensure_ffmpeg()
            if not self.src:
                raise core.TrimmerError("Select a video file first.")
            if not self._valid:
                raise core.TrimmerError("Fix the highlighted segments first.")
        except core.TrimmerError as e:
            self._error(str(e), "Cannot export")
            return
        output = self.output_box.currentData()
        segments = list(self._plan)
        dst = self._choose_destination(output, len(segments))
        if not dst:
            return
        mode = self.mode_box.currentData()
        self._log(
            f"Exporting {len(segments)} part(s) to {self._native(dst)} "
            f"({self.output_box.currentText()}; {self.mode_box.currentText()})"
        )
        self.worker = ExportWorker(self.src, dst, segments, mode, output)
        self.worker.progress.connect(self._on_progress)
        self.worker.succeeded.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.cancelled.connect(self._on_cancelled)
        self.worker.finished.connect(self._on_finished)
        self._set_busy(True)
        self.worker.start()

    def _cancel_export(self) -> None:
        if self.worker and self.cancel_btn.isEnabled():
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setText("Cancelling\u2026")
            self._log("Cancelling export\u2026")
            self.worker.cancel()

    def _on_progress(self, pct: int) -> None:
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
        self.progress.setValue(pct)

    def _on_done(self, paths: list) -> None:
        folder = str(Path(paths[0]).parent) if paths else ""
        text = (
            f"Exported to:\n{self._native(paths[0])}"
            if len(paths) == 1
            else f"Exported {len(paths)} clips to:\n{self._native(folder)}"
        )
        self._log(text.replace("\n", " "))
        box = QMessageBox(
            QMessageBox.Icon.Information, "Export complete", text, parent=self
        )
        reveal = box.addButton("Show in folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is reveal:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _on_failed(self, message: str) -> None:
        self._error(message, "Export failed")

    def _on_cancelled(self) -> None:
        self._log("Export cancelled. Partial output was discarded.")

    def _on_finished(self) -> None:
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        self._set_busy(False)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.worker and self.worker.isRunning():
            answer = QMessageBox.question(
                self, "Export in progress", "Cancel the export and quit?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.cancel()
            self.worker.wait(10000)
        self.preview.shutdown()
        event.accept()
