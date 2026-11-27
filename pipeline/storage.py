"""Keeps finished MP4s between the render run and the Instagram post, as assets on one
GitHub release ("video-buffer") of this repo. Uses the gh CLI with the workflow's token.
Assets are deleted after the Reel is posted."""
from __future__ import annotations

import subprocess
from pathlib import Path

TAG = "video-buffer"


def _gh(*args, check=True):
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=check)


def ensure_release():
    if _gh("release", "view", TAG, check=False).returncode != 0:
        _gh("release", "create", TAG, "--title", "Video buffer (automatic, do not edit)",
            "--notes", "Rendered Shorts waiting for their Instagram slot. Managed by the pipeline.", "--prerelease")


def put(path: Path, name: str) -> str:
    ensure_release()
    dst = Path(path).with_name(name)
    if Path(path) != dst:
        dst.write_bytes(Path(path).read_bytes())
    _gh("release", "upload", TAG, str(dst), "--clobber")
    return name


def get(name: str, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    _gh("release", "download", TAG, "--pattern", name, "--dir", str(dest_dir), "--clobber")
    return dest_dir / name


def delete(name: str):
    _gh("release", "delete-asset", TAG, name, "--yes", check=False)
