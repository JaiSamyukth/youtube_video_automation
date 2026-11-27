"""state/ledger.csv: one row per produced (or failed) Short. The single source of truth
for what is scheduled where, so a retry never double-posts and a slot is never filled twice."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT

LEDGER = ROOT / "state" / "ledger.csv"
FIELDS = ["id", "channel", "theme", "topic_id", "topic", "style", "slot_utc", "status",
          "yt_id", "yt_url", "ig_status", "ig_id", "ig_url", "asset", "title",
          "created_utc", "run_url", "notes"]
# status: scheduled (on YouTube with publishAt) | failed | rendered (made but not uploaded)
# ig_status: pending | published | skipped | failed:<reason>


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def read(path: Path = LEDGER) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [{k: (r.get(k) or "") for k in FIELDS} for r in csv.DictReader(f)]


def write(rows: list[dict], path: Path = LEDGER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    tmp.replace(path)


def upsert(row: dict, path: Path = LEDGER) -> None:
    rows = read(path)
    for i, r in enumerate(rows):
        if r["id"] == row["id"]:
            rows[i] = {**r, **row}
            break
    else:
        rows.append({k: row.get(k, "") for k in FIELDS})
    write(rows, path)


def filled_slots(rows: list[dict], channel: str) -> set[str]:
    return {r["slot_utc"] for r in rows if r["channel"] == channel and r["status"] == "scheduled"}


def _rank(r: dict) -> tuple:
    st = {"scheduled": 3, "rendered": 2, "failed": 1}.get(r["status"], 0)
    ig = 3 if r["ig_status"] == "published" else (2 if r["ig_status"].startswith(("failed", "skipped")) else 1)
    return (st, ig, r["created_utc"])


def merge(theirs: list[dict], mine: list[dict]) -> list[dict]:
    """Combine two ledgers row by row (id); on a clash keep the row that got further."""
    out = {r["id"]: r for r in theirs}
    for r in mine:
        if r["id"] not in out or _rank(r) > _rank(out[r["id"]]):
            out[r["id"]] = r
    return sorted(out.values(), key=lambda r: (r["slot_utc"], r["id"]))
