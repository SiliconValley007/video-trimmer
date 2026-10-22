import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from video_trimmer import quiet

ROOT = Path(__file__).resolve().parent.parent


def _run(code, **env):
    full = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "QT_QPA_PLATFORM": "offscreen",
        **env,
    }
    full.pop("VIDEO_TRIMMER_DEBUG", None) if "VIDEO_TRIMMER_DEBUG" not in env else None
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, env=full
    )


def test_native_stderr_is_silenced_but_python_stderr_and_tracebacks_survive():
    res = _run(
        "import os, sys\n"
        "from video_trimmer import quiet\n"
        "assert quiet.silence_native_stderr()\n"
        "os.write(2, b'NATIVE-NOISE\\n')\n"
        "print('PYTHON-STDERR-OK', file=sys.stderr)\n"
        "raise RuntimeError('boom')\n"
    )
    assert "NATIVE-NOISE" not in res.stderr
    assert "PYTHON-STDERR-OK" in res.stderr and "RuntimeError: boom" in res.stderr


def test_returns_false_without_a_console():
    original = sys.stderr
    with mock.patch.object(quiet.os, "dup", side_effect=OSError(9, "bad fd")):
        assert quiet.silence_native_stderr() is False
    assert sys.stderr is original


def test_returns_false_if_redirect_fails(monkeypatch):
    monkeypatch.setattr(sys, "stderr", sys.stderr)  # restored by monkeypatch
    with (
        mock.patch.object(quiet.os, "dup2", side_effect=OSError(9, "bad fd")),
        mock.patch.object(quiet.os, "fdopen", return_value=mock.Mock()),
    ):
        assert quiet.silence_native_stderr() is False


def test_qt_logging_is_disabled_before_pyqt_is_imported():
    src = (ROOT / "video_trimmer" / "__main__.py").read_text(encoding="utf-8")
    assert src.index('"*=false"') < src.index("from PyQt6")
    res = _run(
        "import os, sys\nassert 'PyQt6' not in sys.modules\n"
        "import video_trimmer.__main__\nprint(os.environ['QT_LOGGING_RULES'])"
    )
    assert res.stdout.strip() == "*=false", res.stderr


def test_debug_env_keeps_qt_logging():
    res = _run(
        "import os\nimport video_trimmer.__main__\nprint(os.environ.get('QT_LOGGING_RULES'))",
        VIDEO_TRIMMER_DEBUG="1",
    )
    assert res.stdout.strip() == "None", res.stderr


_MAIN = (
    "import os\nfrom unittest import mock\nimport video_trimmer.__main__ as m\n"
    "with mock.patch.object(m, 'QApplication') as app, mock.patch.object(m, 'MainWindow'), mock.patch.object(m, 'QIcon'):\n"
    "    app.return_value.exec.return_value = 0\n"
    "    rc = m.main()\n"
    "os.write(2, b'NATIVE-NOISE\\n')\nprint('rc', rc)\n"
)


def test_main_silences_native_stderr():
    res = _run(_MAIN)
    assert "rc 0" in res.stdout and "NATIVE-NOISE" not in res.stderr


def test_main_does_not_silence_in_debug_mode():
    res = _run(_MAIN, VIDEO_TRIMMER_DEBUG="1")
    assert "NATIVE-NOISE" in res.stderr
