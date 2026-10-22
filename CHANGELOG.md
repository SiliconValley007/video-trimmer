# Changelog

## 1.2.0

- Preview frames are rendered by FFmpeg (exact frame, debounced, background thread) when scrubbing, seeking, stepping or pausing, avoiding Windows Media Foundation green-screen artifacts and keyframe lag. Playback still uses QtMultimedia when available.
- Authoritative playhead: `position()` never queries the player; manual seeks ignore player position reports for 300 ms.
- Draggable Start `[` / End `]` handles on the timeline for the selected row (live-updating the table and scrubbing), 10 px edge padding, frame stepping (Alt+Left/Right).
- Add creates a segment at the playhead.
- Silenced native stderr noise (`QT_LOGGING_RULES=*=false` before PyQt imports, FD 2 redirected to the null device; set `VIDEO_TRIMMER_DEBUG=1` to disable).

## 1.1.0

- Segments now default to **cut out (remove)**: list the parts to delete and the rest of the video is exported. "Keep only these segments" remains available.
- Fixed clipped time text while editing a cell.
- Native path separators in the UI, quieter multimedia logging, app icon.
- Windows `.exe` packaging (PyInstaller) and a tag-driven GitHub release workflow.

## 1.0.0

- Initial release: preview, timeline, multi-segment merge/separate export, fast/accurate modes, progress and cancel.
