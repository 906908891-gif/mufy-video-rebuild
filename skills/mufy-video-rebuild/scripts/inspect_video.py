#!/usr/bin/env python3
"""Extract timestamped, full-resolution evidence frames; never edits the source."""
import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path


def command(args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-1800:] or "Command failed")
    return result.stdout


def timestamp(seconds):
    millis = round(seconds * 1000)
    whole, ms = divmod(millis, 1000)
    minutes, sec = divmod(whole, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02}:{minute:02}:{sec:02}.{ms:03}"


def inspect(args):
    for executable in ("ffmpeg", "ffprobe"):
        if not shutil.which(executable):
            raise ValueError(f"Missing executable: {executable}")
    source = Path(args.video).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Video file does not exist: {source}")
    if not math.isfinite(args.interval) or args.interval <= 0:
        raise ValueError("--interval must be positive and finite")
    if args.max_frames < 1:
        raise ValueError("--max-frames must be positive")
    probe = json.loads(command([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)
    ]))
    streams = probe.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("No video stream found")
    duration = float(probe.get("format", {}).get("duration") or video.get("duration") or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Cannot determine a positive video duration")
    if args.times:
        times = [float(value.strip()) for value in args.times.split(",")]
        if any(not math.isfinite(t) or t < 0 or t >= duration for t in times):
            raise ValueError("Every timestamp must be finite and within [0, duration)")
    else:
        count = math.ceil(duration / args.interval)
        if count + 1 > args.max_frames:
            raise ValueError("Too many frames: increase --interval or --max-frames; no tail was skipped")
        times = [index * args.interval for index in range(count)]
        times.append(max(0.0, duration - min(0.25, duration / 2)))
    times = sorted(set(round(t, 6) for t in times))
    if len(times) > args.max_frames:
        raise ValueError("Too many requested timestamps; increase --max-frames explicitly")
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    frames = []
    for seconds in times:
        filename = f"frame-{round(seconds * 1000000):013d}.jpg"
        target = out / filename
        command([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(seconds),
            "-i", str(source), "-map", "0:v:0", "-frames:v", "1", "-q:v", "2", "-y", str(target)
        ])
        if not target.is_file() or not target.stat().st_size:
            raise ValueError(f"No frame at {timestamp(seconds)}; inspect a nearby timestamp")
        frames.append({"seconds": seconds, "timestamp": timestamp(seconds), "file": filename})
    from PIL import Image, ImageDraw
    sheets = []
    columns, tile_width, tile_height, band, per_sheet = 4, 240, 180, 25, 20
    for offset in range(0, len(frames), per_sheet):
        batch = frames[offset:offset + per_sheet]
        rows = math.ceil(len(batch) / columns)
        sheet = Image.new("RGB", (columns * tile_width, rows * (tile_height + band)), "#15151b")
        draw = ImageDraw.Draw(sheet)
        for index, frame in enumerate(batch):
            left, top = index % columns * tile_width, index // columns * (tile_height + band)
            with Image.open(out / frame["file"]) as original:
                thumb = original.convert("RGB")
                thumb.thumbnail((tile_width, tile_height))
                sheet.paste(thumb, (left + (tile_width - thumb.width) // 2, top + (tile_height - thumb.height) // 2))
            draw.text((left + 8, top + tile_height + 5), frame["timestamp"], fill="white")
        filename = f"contact-{offset // per_sheet + 1:03}.jpg"
        sheet.save(out / filename, quality=90)
        sheets.append(filename)
    index = {
        "source": str(source), "duration": duration, "width": video.get("width"),
        "height": video.get("height"), "rotation_metadata": video.get("side_data_list", []),
        "has_audio": any(item.get("codec_type") == "audio" for item in streams),
        "frames": frames, "contact_sheets": sheets,
        "note": "Contact sheets are indexes. Inspect original frames and transitions before reconstruction."
    }
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "frames": len(frames), "duration": duration, "contact_sheets": sheets}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video")
    parser.add_argument("--out", required=True)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--times", help="Explicit seconds, comma-separated, e.g. 3.2,7.8,12.1")
    parser.add_argument("--max-frames", type=int, default=180)
    try:
        inspect(parser.parse_args())
    except (ValueError, RuntimeError, OSError, ImportError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")
