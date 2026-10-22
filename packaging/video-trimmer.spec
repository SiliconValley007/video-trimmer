# PyInstaller spec: `pyinstaller packaging/video-trimmer.spec --noconfirm` -> dist/VideoTrimmer(.exe)
import os

root = os.path.abspath(os.path.join(SPECPATH, ".."))
assets = os.path.join(root, "video_trimmer", "assets")
icon = os.path.join(assets, "icon.ico")

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[root],
    datas=[(assets, "video_trimmer/assets")],
    hiddenimports=["PyQt6.QtMultimedia", "PyQt6.QtMultimediaWidgets"],
    excludes=["tkinter", "unittest", "pytest", "PyQt6.QtWebEngineCore", "PyQt6.QtQml", "PyQt6.QtQuick"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VideoTrimmer",
    console=False,
    icon=icon if os.path.exists(icon) else None,
    upx=False,
)
