"""Per-channel, per-theme topic banks: channels/<channel>/topics-<theme>.csv."""
from __future__ import annotations

import csv
import re
from pathlib import Path

FIELDS = ["id", "topic", "angle", "status", "attempts", "notes"]
MAX_ATTEMPTS = 2


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [{k: (r.get(k) or "") for k in FIELDS} for r in csv.DictReader(f)]


def write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in FIELDS} for r in rows)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def next_pending(path: Path, exclude_ids: set[str] = frozenset()) -> dict | None:
    for r in read(path):
        if r["status"] == "pending" and r["id"] not in exclude_ids and int(r["attempts"] or 0) < MAX_ATTEMPTS:
            return r
    return None


def mark(path: Path, tid: str, status: str, note: str = "") -> None:
    rows = read(path)
    for r in rows:
        if r["id"] == tid:
            if status == "failed":
                r["attempts"] = str(int(r["attempts"] or 0) + 1)
                # a failed topic goes back in the queue until it has failed MAX_ATTEMPTS times
                r["status"] = "pending" if int(r["attempts"]) < MAX_ATTEMPTS else "failed"
            else:
                r["status"] = status
            if note:
                r["notes"] = note[:300]
    write(path, rows)


def pending_count(path: Path) -> int:
    return sum(1 for r in read(path) if r["status"] == "pending")


def add(path: Path, prefix: str, items: list[dict]) -> int:
    """Append new topics, skipping near-duplicates of anything already in the bank."""
    rows = read(path)
    seen = {norm(r["topic"]) for r in rows}
    n = max([int(re.sub(r"\D", "", r["id"]) or 0) for r in rows] + [0])
    added = 0
    for it in items:
        t = str(it.get("topic", "")).strip()
        if not t or norm(t) in seen:
            continue
        n += 1
        rows.append({"id": f"{prefix}{n:03d}", "topic": t, "angle": str(it.get("angle", "")).strip(),
                     "status": "pending", "attempts": "0", "notes": "auto-generated"})
        seen.add(norm(t))
        added += 1
    write(path, rows)
    return added


def merge(theirs: list[dict], mine: list[dict]) -> list[dict]:
    """Union by id; a topic that moved on (done/failed, more attempts) wins over a stale copy."""
    rank = {"pending": 0, "in_progress": 1, "failed": 2, "done": 3}
    out = {r["id"]: r for r in theirs}
    order = [r["id"] for r in theirs]
    for r in mine:
        o = out.get(r["id"])
        if o is None:
            order.append(r["id"])
            out[r["id"]] = r
        elif (rank.get(r["status"], 0), int(r["attempts"] or 0)) > (rank.get(o["status"], 0), int(o["attempts"] or 0)):
            out[r["id"]] = r
    return [out[i] for i in order]
