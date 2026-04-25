---
name: process-video
description: Run the full silence removal pipeline on a video in todo/ - probe specs, prepare per-video intro/outro, detect silence, then remove silence and concat. Use when the user wants to process a new video, run the silence pipeline, do silence removal, or adds a new video to todo/.
---

# Video Pipeline Skill

End-to-end workflow for processing a video through silence detection and removal, optionally with intro/outro.

## When to use

User adds a video to `todo/` and wants any combination of: detect silence, remove silence, merge with intro/outro.

## Inputs to clarify with user

- Video filename (in `todo/`)
- **Threshold:** default 5s. Use 4s for tighter cuts (e.g. lecture-style with short pauses). Use 6-8s for conversations with natural pauses you want to keep.
- **Noise floor:** default -35dB (catches filler "aaaam" sounds). Lower (-40 to -50) for cleaner audio with no background noise. Higher (-30) only if -35 is missing too much.
- Whether intro/outro should be included (both, one, or neither - the script supports omitting)
- Whether to pause for user review of `_review_segments.mp4` before removal, or chain straight through

## Pipeline steps

### Step 1: Probe specs

```bash
ffprobe -v quiet -print_format json -show_streams -show_format "todo/<video>.mp4"
```

Extract: `width`, `height`, `fps`, `pix_fmt`, `sample_rate`, `channels`, `duration`. Record width x height - you need it for step 2.

### Step 2: Prepare intro/outro assets (if user wants them)

**Discover existing assets dynamically** - do not trust any list. List `assets/` and probe each `arnak-*-intro.mp4` to find one with the same width x height:

```bash
for f in assets/arnak-*-intro.mp4; do
  res=$(ffprobe -v quiet -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "$f")
  echo "$(basename "$f"): $res"
done
```

**If a matching resolution exists:** byte-copy it (instant, no encoding, no quality loss):
```bash
cp assets/arnak-XX-intro.mp4 assets/arnak-NN-intro.mp4
cp assets/arnak-XX-outro.mp4 assets/arnak-NN-outro.mp4
```

**If no match:** encode from source templates `assets/intro-attemt-2.mp4` and `assets/outro.mp4`. Run sequentially (NEVER parallel - see hard rules):

```bash
ffmpeg -y -i assets/intro-attemt-2.mp4 \
  -vf "scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:-1:-1:black,setsar=1" \
  -r 25 -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p \
  -c:a aac -ar 48000 -ac 2 \
  assets/arnak-NN-intro.mp4
# Then same for outro from assets/outro.mp4 (sequential, after intro completes).
```

Why libx264 here and not QSV: these are 6-7 second clips. QSV's startup overhead exceeds its encoding speedup at this length. For the long main video in step 5, QSV is the right choice.

### Step 3: Detect silence

Always run in background (long-running). Do NOT pass `--no-gpu` - the script auto-detects QSV.

```bash
python detect_silence.py todo/arnak-NN.mp4 --threshold 5
```

Outputs: `todo/arnak-NN_silence_segments.json` and `todo/arnak-NN_review_segments.mp4`.

Detection runs at roughly 5-10x realtime. Expect ~5-15 min for 1h videos.

**Edge case:** if zero segments are found, the script logs "Nothing detected" and exits with no JSON written. Tell the user and suggest adjusting `--threshold` or `--noise`. Do not proceed to step 5.

### Step 4: (Optional) User review

If user requested review, pause and let them watch `arnak-NN_review_segments.mp4`. Otherwise skip straight to step 5.

### Step 5: Remove silence + concat intro/outro

```bash
python remove_silence.py \
  todo/arnak-NN.mp4 \
  todo/arnak-NN_silence_segments.json \
  -o wip/arnak-NN-cleaned.mp4 \
  --intro assets/arnak-NN-intro.mp4 \
  --outro assets/arnak-NN-outro.mp4
```

Both `--intro` and `--outro` are optional - omit either or both if the user doesn't want them.

Encoding takes 20-60+ min depending on resolution and CPU/GPU load. Background it.

What the script does:
1. Encodes the silence-removed main with QSV (or libx264 fallback) into `wip/_temp_build/arnak-NN_main_cleaned.mp4`.
2. Probes the cleaned output, then **conditionally** re-encodes intro/outro only if their metadata doesn't match (e.g. different encoder produced different pix_fmt nuances). When pre-encoded per-video assets are used, this re-encode is usually skipped.
3. Stream-copy concatenates intro + main + outro (~3s).
4. Keeps `wip/_temp_build/arnak-NN_main_cleaned.mp4` so you can re-concat without re-encoding the long main pass.

## Hard rules

- **Never run two ffmpeg encodes in parallel.** The Iris Xe + thermal limits will lock the machine. We learned this the painful way.
- **Never pass `--no-gpu`** unless the user explicitly asks. QSV auto-detection handles fallback to libx264.
- **Always background long encodes** so the user can monitor progress without blocking.
- **Verify resolution match by probing, not memory.** Copy assets only when width, height, and fps all match.

## Reference: anecdotal timings

These are single-run measurements - useful as ballpark only, real timings depend on QSV availability that day, system load, and source bitrate.

| Resolution | Duration | Encode time | Output size |
|------------|----------|-------------|-------------|
| 1920x1080  | 46 min   | 21 min      | 104 MB      |
| 1664x1920  | 1h 16m   | 30 min      | 334 MB      |
| 1760x1920  | 1h 14m   | 30 min      | 193 MB      |
| 3414x1920  | 1h 7m    | 59 min      | 269 MB      |

Ultra-wide (3414x1920) costs roughly 2x the encoding time of standard portrait.
