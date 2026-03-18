#!/usr/bin/env python3
"""
Detect silence/filler segments in a video and optionally create a review video.

Usage:
    python detect_silence.py <input_video> [--threshold 5] [--noise -35]
    python detect_silence.py <input_video> --threshold 3 --no-review

Examples:
    python detect_silence.py wip/sevak_20_sample.mp4 --threshold 5
    python detect_silence.py wip/sevak_20_sample.mp4 --threshold 1 --noise -30
"""

import subprocess
import json
import re
import sys
import os
import time
import argparse
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
        except Exception:
            continue
    return None


def video_encoder_args(crf, hw_encoder):
    """Return encoder flags for the detected encoder (or CPU fallback)."""
    if hw_encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(crf)]
    if hw_encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "faster", "-global_quality", str(crf)]
    return ["-c:v", "libx264", "-preset", "fast", "-crf", str(crf)]


# ── Configurable defaults ──────────────────────────────────────────
DEFAULT_THRESHOLD = 5.0   # minimum silence duration in seconds
DEFAULT_NOISE_DB = -35    # dB threshold (higher = catches more, e.g. "aaaam")
# ───────────────────────────────────────────────────────────────────


def fmt_time(s):
    """Format seconds as HH:MM:SS.mmm"""
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:06.3f}"


def detect_silence(input_path, noise_db, min_duration, log):
    """Run ffmpeg silencedetect and parse output."""
    log.info(f"Running ffmpeg silencedetect (noise={noise_db}dB, min_duration={min_duration}s)...")
    log.debug(f"Input: {input_path}")

    cmd = [
        "ffmpeg", "-i", input_path,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null", "-"
    ]
    log.debug(f"CMD: {' '.join(cmd)}")

    t0 = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - t0

    log.debug(f"ffmpeg silencedetect finished in {elapsed:.1f}s (exit code {result.returncode})")

    if result.returncode != 0:
        log.warning(f"ffmpeg returned non-zero exit code {result.returncode}")
        log.debug(f"ffmpeg stderr (last 500 chars): {result.stderr[-500:]}")

    stderr = result.stderr
    segments = []
    silence_start = None

    for line in stderr.split("\n"):
        start_match = re.search(r"silence_start:\s*([\d.]+)", line)
        end_match = re.search(
            r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)", line
        )

        if start_match:
            silence_start = float(start_match.group(1))
        elif end_match and silence_start is not None:
            silence_end = float(end_match.group(1))
            duration = float(end_match.group(2))
            seg_start = round(silence_start, 3)
            seg_end = round(silence_end, 3)
            seg_dur = round(duration, 3)
            idx = len(segments)
            segments.append({
                "index": idx,
                "start": seg_start,
                "end": seg_end,
                "duration": seg_dur,
                "start_display": fmt_time(seg_start),
                "end_display": fmt_time(seg_end),
                "action": "remove"
            })
            log.debug(f"  Segment {idx}: {fmt_time(seg_start)} - {fmt_time(seg_end)} ({seg_dur:.1f}s)")
            silence_start = None

    total_silence = sum(s["duration"] for s in segments)
    log.info(f"Detection complete in {elapsed:.1f}s — found {len(segments)} segments, total silence {total_silence:.1f}s")
    return segments


def get_fps(input_path):
    """Get frame rate from input video via ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", input_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        data = json.loads(result.stdout)
        for s in data.get("streams", []):
            if s["codec_type"] == "video":
                num, den = s["r_frame_rate"].split("/")
                return round(int(num) / int(den), 3)
    return 25  # fallback


def create_review_video(input_path, segments, output_dir, log, hw_encoder=None):
    """Create a concatenated review video with index overlays."""
    if not segments:
        log.info("No segments found — nothing to review.")
        return None

    output_dir = Path(output_dir)
    temp_dir = output_dir / "_temp_segments"
    temp_dir.mkdir(parents=True, exist_ok=True)

    fps = get_fps(input_path)
    log.debug(f"Detected fps: {fps}")

    # Windows font path for drawtext
    font_file = "C\\\\:/Windows/Fonts/arial.ttf"

    segment_files = []
    total = len(segments)
    t0 = time.time()

    for seg in segments:
        idx = seg["index"]
        start = seg["start"]
        duration = seg["duration"]
        temp_file = temp_dir / f"seg_{idx:04d}.mp4"

        drawtext = (
            f"drawtext=fontfile={font_file}"
            f":text='{idx}'"
            f":fontsize=160:fontcolor=yellow"
            f":borderw=5:bordercolor=black"
            f":x=(w-text_w)/2:y=(h-text_h)/2"
        )

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", input_path,
            "-t", str(duration),
            "-vf", drawtext,
            *video_encoder_args(23, hw_encoder),
            "-c:a", "aac", "-b:a", "128k",
            "-r", str(fps),
            str(temp_file),
        ]

        seg_t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        seg_elapsed = time.time() - seg_t0

        if proc.returncode != 0:
            log.warning(f"  [{idx+1}/{total}] FAILED segment {idx}: {proc.stderr[-200:]}")
            continue

        segment_files.append(temp_file)
        elapsed_total = time.time() - t0
        avg = elapsed_total / (idx + 1)
        eta = avg * (total - idx - 1)
        log.info(f"  [{idx+1}/{total}] {fmt_time(start)} - {fmt_time(seg['end'])} ({duration:.1f}s) — encoded in {seg_elapsed:.1f}s, ETA: {eta:.0f}s")
        log.debug(f"    CMD: {' '.join(cmd)}")

    if not segment_files:
        log.error("No segments were extracted successfully.")
        return None

    # Write concat list
    concat_list = temp_dir / "concat.txt"
    with open(concat_list, "w") as f:
        for sf in segment_files:
            safe_path = str(sf.resolve()).replace("\\", "/")
            f.write(f"file '{safe_path}'\n")

    # Concatenate all segments
    review_path = output_dir / "review_segments.mp4"
    log.info(f"Concatenating {len(segment_files)} segments into review video...")
    concat_t0 = time.time()
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(review_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    concat_elapsed = time.time() - concat_t0

    if proc.returncode != 0:
        log.error(f"Concat error:\n{proc.stderr[-500:]}")
        return None

    log.info(f"Concat done in {concat_elapsed:.1f}s")

    # Cleanup temp files
    log.debug("Cleaning up temp segment files...")
    for sf in segment_files:
        sf.unlink(missing_ok=True)
    concat_list.unlink(missing_ok=True)
    try:
        temp_dir.rmdir()
    except OSError:
        pass

    total_elapsed = time.time() - t0
    log.info(f"Review video saved: {review_path} (total time: {total_elapsed:.1f}s)")
    return review_path


def main():
    parser = argparse.ArgumentParser(
        description="Detect silence/filler segments and build a review video"
    )
    parser.add_argument("input", help="Input video file")
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_THRESHOLD,
        help=f"Min silence duration in seconds (default: {DEFAULT_THRESHOLD})"
    )
    parser.add_argument(
        "--noise", type=float, default=DEFAULT_NOISE_DB,
        help=f"Noise floor in dB — higher catches filler sounds (default: {DEFAULT_NOISE_DB})"
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="Output directory (default: same folder as input)"
    )
    parser.add_argument(
        "--no-review", action="store_true",
        help="Skip building the review video"
    )
    parser.add_argument(
        "--no-gpu", action="store_true",
        help="Disable GPU encoding (force CPU libx264)"
    )
    args = parser.parse_args()

    log = setup_logger("detect_silence")
    log.info(f"=== detect_silence.py started ===")

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

    log.info(f"Args: threshold={args.threshold}s, noise={args.noise}dB, no_review={args.no_review}")

    input_path = os.path.abspath(args.input)
    if not os.path.isfile(input_path):
        log.error(f"File not found: {input_path}")
        sys.exit(1)

    output_dir = args.output_dir or os.path.dirname(input_path)
    os.makedirs(output_dir, exist_ok=True)

    # ── Step 1: detect silence ──
    log.info(f"Analyzing: {input_path}")
    log.info(f"Settings : threshold={args.threshold}s, noise={args.noise}dB")
    run_start = time.time()
    segments = detect_silence(input_path, args.noise, args.threshold, log)

    if not segments:
        log.info("Nothing detected. Try lowering --threshold or raising --noise.")
        sys.exit(0)

    # ── Step 2: save JSON ──
    json_path = os.path.join(output_dir, "silence_segments.json")
    payload = {
        "input": input_path,
        "threshold_seconds": args.threshold,
        "noise_db": args.noise,
        "total_segments": len(segments),
        "segments": segments,
    }
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2)
    log.info(f"Segments JSON saved: {json_path}")

    # ── Step 3: optionally build review video ──
    if args.no_review:
        log.info("Skipping review video (--no-review).")
    else:
        log.info("Building review video...")
        create_review_video(input_path, segments, output_dir, log, hw_encoder)

    total_elapsed = time.time() - run_start
    log.info(f"=== detect_silence.py finished in {total_elapsed:.1f}s ===")


if __name__ == "__main__":
    main()
