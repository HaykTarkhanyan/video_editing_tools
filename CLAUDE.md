# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Two-stage video silence removal pipeline. Stage 1 detects silence segments, stage 2 removes them and optionally adds intro/outro. No pip dependencies - just Python stdlib + ffmpeg/ffprobe via subprocess.

## Commands

```bash
# Stage 1: Detect silence and generate review video
python detect_silence.py <input.mp4> [--threshold 5] [--noise -35] [--output-dir DIR] [--no-review] [--no-gpu]

# Stage 2: Remove detected silence, optionally prepend intro / append outro
python remove_silence.py <input.mp4> <segments.json> -o output.mp4 [--intro FILE] [--outro FILE] [--no-gpu]
```

No build step, no tests, no linting configured. Scripts are run directly.

## Architecture

**detect_silence.py** -> runs ffmpeg silencedetect filter -> parses stderr with regex -> writes `silence_segments.json` + optional `review_segments.mp4` (numbered overlays on each silence clip for visual QA).

**remove_silence.py** -> reads segments JSON -> builds ffmpeg `select`/`aselect` filter expressions with `between()` to keep non-silent intervals -> single-pass encode -> concat intro/outro via stream-copy (concat demuxer). Intro/outro are re-encoded to match main video metadata (resolution, fps, pixel format, sample rate, channels) only if they don't already match.

**log_utils.py** -> shared `setup_logger(name)` that writes DEBUG to `wip/<name>_<timestamp>.log` and INFO to console.

## Key Design Decisions

- **GPU: Intel Iris Xe (integrated, no CUDA)**. NVENC is not available. QSV (h264_qsv) is the only HW encoder option; otherwise falls back to libx264 CPU. Always use `--no-gpu` or QSV - never assume NVENC.
- **Never run multiple ffmpeg encodes in parallel.** The machine cannot handle it - run sequentially and ask user before starting intensive work.
- Intro/outro concat uses stream-copy (near-instant) after ensuring format compatibility via conditional re-encode.
- Segments JSON uses `"action": "remove"` convention - `build_keep_intervals()` inverts these to keep-ranges.
- Default silence threshold: 5s duration, -35 dB noise floor (tuned for catching filler sounds like "aaaam").

## External Dependencies

ffmpeg and ffprobe must be on PATH.

## File Conventions

- `assets/` - intro/outro template videos (tracked in git despite .gitignore video exclusion). Per-video variants named `arnak-NN-intro.mp4` / `arnak-NN-outro.mp4` are pre-encoded to match each video's resolution for instant stream-copy concat.
- `todo/` - raw videos awaiting processing (not tracked). Current batch: arnak-04 through arnak-08 (iPad recordings, mostly portrait, varying resolutions).
- `wip/` - working directory for logs and temp files (not tracked)

## Video Specs

Source videos (arnak series) are iPad recordings with inconsistent resolutions. Always probe before assuming dimensions:
- arnak-04/06: 1664x1920 (portrait)
- arnak-05: 1760x1920 (portrait)
- arnak-07: 3414x1920 (ultra-wide composite)
- arnak-08: 1920x1080 (landscape)
- All share: h264, yuv420p, 25fps, aac 48kHz stereo
