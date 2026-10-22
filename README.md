# Video Trimmer

Cross-platform (Windows, macOS, Linux) desktop app that cuts multiple segments from a video and exports them as one concatenated file. Built with Python, PyQt6 and FFmpeg.

## Features

- Drag & drop (or Browse) a video; duration, resolution, codec, frame rate (incl. VFR) and audio are probed automatically.
- Exact-frame preview (rendered by FFmpeg while scrubbing/pausing, so no codec artifacts) with a timeline that shows your segments and draggable `[` `]` handles for the selected row, plus **Set Start `[`** / **Set End `]`** from the playhead. Falls back gracefully when no multimedia backend is available.
- Flexible times: `HH:MM:SS`, `MM:SS`, `SS`, with optional milliseconds (`.ms`). Live per-row length and total output length.
- Segment table: add, remove, clear all, move up/down, and inline status badges. Invalid, out-of-bounds and overlapping segments are highlighted before export.
- **Output:** merge into one video, or export each clip as a separate file.
- **Method:** frame-accurate re-encode (H.264/AAC, variable frame rate normalised) or fast stream copy (lossless, snaps to keyframes).
- Non-blocking export with real FFmpeg percentage progress, activity log and instant **Cancel Export**. Temporary files are always cleaned up; outputs are only written on success.
- Handles video-only files, never flashes console windows on Windows, and shows actionable errors (missing FFmpeg, permissions, codecs, corrupted files).
- Dark theme, tooltips and keyboard shortcuts.

## Cut vs keep

By default the listed segments are **cut out**: add `00:00:00 -> 00:00:01` and `<last second>` to trim one second from both ends and export everything in between. Switch **Segments** to _Keep only these segments_ to export just the listed parts instead.

## Setup

1. Install Python 3.9+.
2. Install FFmpeg (needs `ffmpeg` and `ffprobe`):
   - Windows: `winget install Gyan.FFmpeg`
   - macOS: `brew install ffmpeg`
   - Linux: `sudo apt install ffmpeg`
3. Install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
4. Run: `python -m video_trimmer` (or `pip install .` then `video-trimmer`).

If FFmpeg is installed but not detected, restart your terminal/IDE or set `FFMPEG_BINARY` and `FFPROBE_BINARY` to the full executable paths.

## Shortcuts

| Action                      | Key                    |
| --------------------------- | ---------------------- |
| Open video                  | Ctrl+O                 |
| Add / Remove segment        | Ctrl+N / Delete        |
| Move segment up / down      | Ctrl+Up / Ctrl+Down    |
| Play / pause                | Space                  |
| Seek -1 s / +1 s            | Ctrl+Left / Ctrl+Right |
| Step -1 / +1 frame          | Alt+Left / Alt+Right   |
| Set start / end at playhead | `[` / `]`              |
| Export / Cancel export      | Ctrl+E / Esc           |
| Quit                        | Ctrl+Q                 |

Clicking a Start/End cell jumps the preview to that time. Separate clips are named `<video>_clip001.<ext>`; merged output goes to `.mp4`, `.mkv` or `.mov`.

## Build a Windows .exe (GitHub release)

```bash
pip install -r requirements.txt pyinstaller
pyinstaller packaging/video-trimmer.spec --noconfirm   # -> dist/VideoTrimmer.exe
```

## Tests

```bash
pip install -r requirements.txt
pytest
```

GUI tests run headless (`QT_QPA_PLATFORM=offscreen`); integration tests run only when FFmpeg is installed.

## Troubleshooting

Set `VIDEO_TRIMMER_DEBUG=1` to show native console output (FFmpeg/Qt logs), which is silenced by default.

## License

MIT
