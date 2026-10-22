"""Dark-mode Qt stylesheet."""

from pathlib import Path

_ARROW = (Path(__file__).parent / "assets" / "arrow_down.png").as_posix()

_TEMPLATE = """
* { font-size: 13px; }
QMainWindow, QDialog, QMessageBox { background: #1b1c23; }
QWidget { color: #e6e6eb; }
QLabel { background: transparent; }
QLabel#muted { color: #8b8da0; }
QLabel#error { color: #ff7b7b; }
QLabel#ok { color: #6fdc8c; }
QLabel#frame { background: #000000; border-radius: 10px; }
QLabel#placeholder { background: #14151a; border-radius: 10px; color: #8b8da0; padding: 16px; }
QSplitter::handle { background: transparent; height: 8px; }
QSplitter::handle:hover { background: #3a3b4a; }
QLabel#title { font-size: 15px; font-weight: 600; }
QFrame#drop { background: #20212b; border: 2px dashed #3a3b4a; border-radius: 12px; }
QFrame#drop[dragging="true"] { border-color: #5c7cfa; background: #232640; }
QLineEdit, QComboBox, QPlainTextEdit {
    background: #272833; border: 1px solid #3a3b4a; border-radius: 6px; padding: 6px 8px;
    selection-background-color: #4c6ef5; selection-color: #ffffff;
}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border-color: #5c7cfa; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow { image: url(@ARROW@); width: 16px; height: 16px; }
QComboBox QAbstractItemView { background: #272833; border: 1px solid #3a3b4a; selection-background-color: #4c6ef5; }
QPushButton {
    background: #2f3040; border: 1px solid #3f4054; border-radius: 6px; padding: 7px 16px;
}
QPushButton:hover { background: #3a3b50; }
QPushButton:pressed { background: #262736; }
QPushButton:disabled { color: #6b6d80; background: #25262f; border-color: #2e2f3b; }
QPushButton#primary { background: #4c6ef5; border-color: #4c6ef5; color: #ffffff; font-weight: 600; }
QPushButton#primary:hover { background: #5c7cfa; }
QPushButton#primary:disabled { background: #2f3550; border-color: #2f3550; color: #7b809c; }
QPushButton#danger { background: #c92a2a; border-color: #c92a2a; color: #ffffff; font-weight: 600; }
QPushButton#danger:hover { background: #e03131; }
QPushButton#danger:disabled { background: #4a2a2e; border-color: #4a2a2e; color: #8a6b6e; }
QTableWidget {
    background: #20212b; alternate-background-color: #23242f; border: 1px solid #3a3b4a;
    border-radius: 8px; gridline-color: #2e2f3b; selection-background-color: #34406e;
    selection-color: #ffffff;
}
QTableWidget::item { padding: 4px 8px; }
QTableWidget QLineEdit {
    background: #14151a; border: 1px solid #5c7cfa; border-radius: 0; margin: 0; padding: 0 8px; min-height: 0;
}
QHeaderView { background: #20212b; }
QHeaderView::section {
    background: #272833; color: #a9abbd; border: none; border-bottom: 1px solid #3a3b4a;
    padding: 7px 8px; font-weight: 600;
}
QTableCornerButton::section { background: #272833; border: none; }
QProgressBar {
    background: #272833; border: 1px solid #3a3b4a; border-radius: 6px; text-align: center;
    min-height: 18px; color: #ffffff;
}
QProgressBar::chunk { background: #4c6ef5; border-radius: 5px; }
QMenuBar { background: #1b1c23; }
QMenuBar::item:selected, QMenu::item:selected { background: #34406e; }
QMenu { background: #272833; border: 1px solid #3a3b4a; padding: 4px; }
QMenu::item { padding: 6px 24px; border-radius: 4px; }
QToolTip { background: #272833; color: #e6e6eb; border: 1px solid #4c6ef5; padding: 4px; }
QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar::handle:vertical { background: #3f4054; border-radius: 5px; min-height: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
"""

STYLE = _TEMPLATE.replace("@ARROW@", _ARROW)
