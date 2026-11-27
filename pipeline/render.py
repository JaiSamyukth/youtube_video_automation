"""Hand the finished script + narration to an OpenCode agent that draws and renders the
video with the stickman-video-director Skill. Script writing and narration are already done
(deterministically) before this step, so the agent only does visuals: far less to go wrong
than the original bot, where one agent session did everything."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from .config import ROOT

STYLE_NAMES = {
    "B7": "B7 Pixel Theater",
    "A5": "Style 5 Historical Cartoon (code-rendered approximation)",
}
ROUTES = {
    "B7": ("B7 is a project-local code style: follow references/b7-pixel-theater.md and its 9:16 portrait contract "
           "references/b7-native-portrait.md (native 180x320 canvas enlarged 6x to 1080x1920), using the Skill's "
           "bundled pixel/ending helpers in scripts/b7_pixel_primitives.py and scripts/b7_reflective_ending.py with "
           "reviewed project-local code. For coloured-word captions use draw_word_caption(image, full_line, "
           "highlight_index, y=264) from {work}/project/b7_pixel_primitives.py (already patched): pass the complete "
           "line WITH spaces; if it overflows, split at an earlier semantic boundary, never delete spaces. "
           "Skip the shared project.json/autopilot.mjs commands; they do not apply to B7."),
    "A5": ("A5 is a Model Engine style that normally needs external AI video generation, which is NOT available "
           "here. Build it with project-local code instead: draw each scene as 2D vector/canvas frames at native "
           "1080x1920 (the Skill's node runtime has @napi-rs/canvas and sharp; rasterised SVG is fine), following "
           "references/historical-cartoon.md and references/a1-a5-direction.md for the drawing grammar only "
           "(character look, palette, painted-style backgrounds, expressive simple faces, caption-safe layout). "
           "Animate with real motion (walks, gestures, object moves, camera pans), mux with the narration, encode "
           "with ffmpeg. State in NOTES.md that this is a code-rendered approximation of the style."),
}
STALL_SECONDS = 480
OPENCODE_PERMS = '{"permission":{"external_directory":"allow","bash":"allow","edit":"allow","webfetch":"deny"}}'


def build_prompt(work: Path, skill_dir: Path, style: str, duration: float) -> str:
    tpl = (ROOT / "prompts" / "render-agent.md").read_text(encoding="utf-8")
    route = ROUTES.get(style, ROUTES["B7"]).replace("{work}", str(work))
    return tpl.format(skill_dir=skill_dir, style=style, style_name=STYLE_NAMES.get(style, style), work=work,
                      duration=duration, route=route)


def _activity(work: Path) -> tuple:
    newest = 0.0
    for p in work.rglob("*"):
        if p.is_file() and p.name != "agent.log":
            try:
                newest = max(newest, p.stat().st_mtime)
            except OSError:
                pass
    log = work / "out" / "agent.log"
    return (log.stat().st_size if log.exists() else 0, newest)


def _video_ok(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 200_000:
        return False
    try:
        d = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                           "-of", "csv=p=0", str(path)]).strip())
        return d >= 20
    except Exception:
        return False


def _run(cmd: list[str], work: Path, limit: int, log=print) -> str:
    """Run one agent session with a stall watchdog. Returns 'done' | 'stall' | 'timeout' | 'exit'."""
    env = {**os.environ, "OPENCODE_CONFIG_CONTENT": OPENCODE_PERMS}
    with (work / "out" / "agent.log").open("a") as lf:
        proc = subprocess.Popen(cmd, cwd=work, env=env, stdout=lf, stderr=subprocess.STDOUT,
                                start_new_session=True)
        t0 = last_change = time.time()
        last = _activity(work)
        while proc.poll() is None:
            time.sleep(20)
            now, cur = time.time(), _activity(work)
            if cur != last:
                last, last_change = cur, now
            if _video_ok(work / "out" / "video.mp4") and (work / "out" / "NOTES.md").exists():
                time.sleep(15)
                _kill(proc)
                return "done"
            if now - last_change > STALL_SECONDS:
                log("agent stalled; restarting with a fresh session")
                _kill(proc)
                return "stall"
            if now - t0 > limit:
                _kill(proc)
                return "timeout"
    return "exit"


def _kill(proc):
    try:
        os.killpg(proc.pid, 9)
    except Exception:
        proc.kill()


def render(work: Path, skill_dir: Path, style: str, duration: float, model: str,
           budget_seconds: int = 5400, log=print) -> Path:
    work = Path(work)
    (work / "out").mkdir(parents=True, exist_ok=True)
    (work / "tmp").mkdir(exist_ok=True)
    (work / "project").mkdir(exist_ok=True)
    shutil.copy(ROOT / "prompts" / "content-policy.md", work / "content-policy.md")
    if style == "B7":
        from .b7_patch import install
        install(skill_dir, work / "project")
    prompt = build_prompt(work, skill_dir, style, duration)
    (work / "PROMPT.md").write_text(prompt, encoding="utf-8")
    start, stalls = time.time(), 0
    for attempt in range(1, 7):
        if _video_ok(work / "out" / "video.mp4"):
            break
        left = int(budget_seconds - (time.time() - start))
        if left < 300 or stalls >= 4:
            break
        if attempt == 1:
            msg = prompt
            cmd = ["opencode", "run", "-m", model, msg]
        elif stalls:
            # A stalled session is never resumed (replaying its history hung every time in the old bot).
            msg = ("A previous session of this job hung. Start fresh from the files on disk and do not redo work "
                   f"that exists. Read {work}/PROMPT.md for the full assignment, inspect {work}/project, {work}/tmp "
                   f"and {work}/out, then finish: render {work}/out/video.mp4 at exactly 1080x1920 with the existing "
                   "narration and write NOTES.md. Never open image files.")
            cmd = ["opencode", "run", "-m", model, msg]
        else:
            msg = (f"You stopped before finishing. {work}/out/video.mp4 does not exist yet. Keep calling tools until "
                   f"it is rendered at 1080x1920 and NOTES.md is written. Re-read {work}/PROMPT.md if needed.")
            cmd = ["opencode", "run", "-c", "-m", model, msg]
        log(f"render attempt {attempt} ({left}s left)")
        res = _run(cmd, work, left, log)
        if res == "stall":
            stalls += 1
            time.sleep(60)
    out = work / "out" / "video.mp4"
    if not _video_ok(out):
        raise RuntimeError("the render agent did not produce a playable video")
    return out
