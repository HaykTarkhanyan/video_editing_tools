#!/usr/bin/env python3
"""Speed up (or slow down) a video using ffmpeg.

By default the audio pitch is preserved, so the voice does not become
cartoonish at high speeds. Pass --no-preserve-pitch for the classic
"chipmunk" effect.

Examples:
    python speedup_video.py lecture.mp4 -s 1.5
    python speedup_video.py lecture.mp4 -s 2.0 -o fast.mp4
    python speedup_video.py lecture.mp4 -s 2.0 --no-preserve-pitch

Status: NOT YET TESTED on a real video. Syntax, CLI parsing, and the
atempo-chain logic were verified, but no end-to-end run against an
actual file has happened. Known limitations:
  - operates only on the first audio stream (a:0)
  - fails fast if the input has no audio
"""

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

from log_utils import setup_logger


def detect_hw_encoder():
    """Detect the best available hardware encoder. Returns encoder name or None."""
    candidates = [
        ("h264_nvenc", ["ffmpeg", "-f", "lavfi", "-i", "nullsrc=s=256x256:d=0.1",
                        "-c:v", "h264_nvenc", "-preset", "p4", "-f", "null", "-"]),
        ("h264_qsv",  ["ffmpeg", "-f", "lavfi", "-i", "nullsrc=s=256x256:d=0.1",
                        "-c:v", "h264_qsv", "-preset", "faster", "-f", "null", "-"]),
    ]
    for name, cmd in candidates:
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=10)
            if proc.returncode == 0:
                return name
        except subprocess.TimeoutExpired:
            continue
        except FileNotFoundError:
            raise RuntimeError("ffmpeg not found on PATH")
    return None


def video_encoder_args(crf, hw_encoder):
    """Return encoder flags for the detected encoder (or CPU fallback)."""
    if hw_encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(crf)]
    if hw_encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "faster", "-global_quality", str(crf)]
    return ["-c:v", "libx264", "-preset", "fast", "-crf", str(crf)]


def ffmpeg_has_filter(name):
    """Return True if `name` is listed by `ffmpeg -filters`."""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        capture_output=True, text=True, check=True,
    ).stdout
    for line in out.splitlines():
        parts = line.split()
        # Lines look like: " ..C atempo            A->A       Adjust audio tempo."
        if len(parts) >= 2 and parts[1] == name:
            return True
    return False


def probe_audio_rate(path):
    """Return the sample rate of the first audio stream."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if not out:
        raise RuntimeError(f"no audio stream found in {path}")
    return int(out)


def chain_atempo(speed):
    """Chain `atempo` filters so each link stays in the high-quality 0.5-2.0 range.

    `atempo` accepts 0.5..100 in a single call, but quality degrades fast
    outside ~[0.5, 2.0]. Chaining is the documented workaround.
    """
    parts = []
    remaining = speed
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.6f}")
    return ",".join(parts)


def build_audio_filter(speed, preserve_pitch, sample_rate, use_rubberband):
    if not preserve_pitch:
        # Reinterpret samples at a higher rate -> faster + higher pitch.
        # Resample back to a standard rate so the container is happy.
        return f"asetrate={sample_rate}*{speed},aresample={sample_rate}"
    if use_rubberband:
        return f"rubberband=tempo={speed}"
    return chain_atempo(speed)


def main():
    parser = argparse.ArgumentParser(
        description="Speed up a video, optionally preserving audio pitch.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="Input video file")
    parser.add_argument("-o", "--output", default=None,
                        help="Output file (default: <input>_<speed>x.<ext>)")
    parser.add_argument("-s", "--speed", type=float, default=1.5,
                        help="Speed multiplier, e.g. 1.2, 2.0 (default: 1.5)")
    parser.add_argument("--no-preserve-pitch", action="store_true",
                        help="Let pitch rise with speed (chipmunk effect)")
    parser.add_argument("--no-rubberband", action="store_true",
                        help="Use atempo even when rubberband is available")
    parser.add_argument("--no-gpu", action="store_true",
                        help="Disable GPU encoding (force CPU libx264)")
    args = parser.parse_args()

    log = setup_logger("speedup_video")
    log.info("=== speedup_video.py started ===")

    if args.speed <= 0:
        log.error(f"speed must be positive, got {args.speed}")
        sys.exit(1)
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        log.error("ffmpeg/ffprobe not found on PATH")
        sys.exit(1)

    input_path = Path(args.input)
    if not input_path.exists():
        log.error(f"input not found: {input_path}")
        sys.exit(1)

    if args.output:
        output_path = Path(args.output)
    else:
        tag = f"{args.speed:g}x".replace(".", "p")
        output_path = input_path.with_name(f"{input_path.stem}_{tag}{input_path.suffix}")

    # ── GPU detection ──
    if args.no_gpu:
        hw_encoder = None
        log.info("GPU encoding disabled by --no-gpu flag")
    else:
        log.info("Checking for hardware encoders (NVENC, QSV)...")
        hw_encoder = detect_hw_encoder()
        if hw_encoder:
            log.info(f"Hardware encoding ENABLED ({hw_encoder})")
        else:
            log.info("No hardware encoder available — using CPU (libx264)")

    preserve_pitch = not args.no_preserve_pitch
    use_rubberband = (not args.no_rubberband) and ffmpeg_has_filter("rubberband")
    sample_rate = probe_audio_rate(input_path)

    if preserve_pitch and use_rubberband:
        engine = "rubberband"
    elif preserve_pitch:
        engine = "atempo"
    else:
        engine = "asetrate (chipmunk)"

    log.info(f"Input:           {input_path}")
    log.info(f"Output:          {output_path}")
    log.info(f"Speed:           {args.speed}x")
    log.info(f"Preserve pitch:  {preserve_pitch}")
    log.info(f"Audio engine:    {engine}")
    log.info(f"Sample rate:     {sample_rate} Hz")

    video_filter = f"setpts=PTS/{args.speed}"
    audio_filter = build_audio_filter(args.speed, preserve_pitch, sample_rate, use_rubberband)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-filter:v", video_filter,
        "-filter:a", audio_filter,
        *video_encoder_args(18, hw_encoder),
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path),
    ]
    log.debug(f"CMD: {' '.join(cmd)}")
    log.info("Running ffmpeg...")
    encode_start = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - encode_start

    if proc.returncode != 0:
        log.error(f"ffmpeg failed (exit {proc.returncode}) after {elapsed:.1f}s")
        log.error(f"stderr tail: {proc.stderr[-500:]}")
        sys.exit(proc.returncode or 1)

    log.info(f"Encoding done in {elapsed:.1f}s")
    log.info(f"Output: {output_path}")
    log.info("=== speedup_video.py finished ===")


if __name__ == "__main__":
    main()
