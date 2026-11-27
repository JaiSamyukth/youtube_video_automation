"""Numeric checks on the finished MP4. A video that fails is never uploaded."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


def probe(path: Path) -> dict:
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
                                   "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate",
                                   "-of", "json", str(path)])
    return json.loads(out)


def scene_changes(path: Path) -> int:
    """Count set/shot changes: sample one frame a second from the upper 60% of the frame (so caption swaps
    don't count) and count jumps in mean pixel difference over 10/255. ffmpeg's own scene score misses
    the dark, low-contrast B7 sets, so we measure the difference directly."""
    import tempfile
    from PIL import Image, ImageChops, ImageStat
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-vf",
                        "fps=1,crop=iw:ih*0.6:0:0,scale=54:60", f"{d}/%04d.png"], check=True)
        prev, cuts = None, 0
        for f in sorted(Path(d).glob("*.png")):
            im = Image.open(f).convert("RGB")
            if prev is not None and sum(ImageStat.Stat(ImageChops.difference(im, prev)).mean) / 3 > 10:
                cuts += 1
            prev = im
    return cuts


def black_fraction(path: Path, duration: float) -> float:
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path), "-vf", "blackdetect=d=0.3:pix_th=0.08",
                        "-an", "-f", "null", "-"], capture_output=True, text=True)
    total = sum(float(x) for x in re.findall(r"black_duration:([\d.]+)", r.stderr))
    return total / max(duration, 0.1)


def max_volume(path: Path) -> float:
    r = subprocess.run(["ffmpeg", "-i", str(path), "-af", "volumedetect", "-vn", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"max_volume:\s*(-?[\d.]+) dB", r.stderr)
    return float(m.group(1)) if m else -99.0


def fix_size(path: Path) -> bool:
    """Safety net: scale a non-1080x1920 render up rather than throwing away a good film."""
    tmp = path.with_name("fixed.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vf",
                    "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30",
                    "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                    "-movflags", "+faststart", str(tmp)], check=True)
    tmp.replace(path)
    return True


def check(path: Path, narration_seconds: float) -> dict:
    path = Path(path)
    info = probe(path)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    problems, notes = [], []
    if v and (v["width"], v["height"]) != (1080, 1920):
        notes.append(f"rescaled from {v['width']}x{v['height']}")
        fix_size(path)
        info = probe(path)
        v = next(s for s in info["streams"] if s["codec_type"] == "video")
    dur = float(info["format"]["duration"])
    if not v:
        problems.append("no video stream")
    if not a:
        problems.append("no audio stream")
    if not (narration_seconds - 1.0 <= dur <= narration_seconds + 3.0):
        problems.append(f"duration {dur:.1f}s does not match narration {narration_seconds:.1f}s")
    if dur > 60:
        problems.append(f"{dur:.1f}s is over the 60s Shorts limit")
    vol = max_volume(path) if a else -99
    if vol < -40:
        problems.append(f"audio is near silent (max {vol} dB)")
    cuts = scene_changes(path)
    if cuts < 5:
        problems.append(f"only {cuts} visual scene changes; the film is too static")
    black = black_fraction(path, dur)
    if black > 0.08:
        problems.append(f"{black:.0%} of the film is black")
    return {"ok": not problems, "problems": problems, "notes": notes, "duration": round(dur, 2),
            "scene_changes": cuts, "black_fraction": round(black, 3), "max_volume_db": vol}
