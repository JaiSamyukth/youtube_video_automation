"""Posting slots and the producer's plan: which channel needs the next video, for which slot.

Shorts are uploaded to YouTube as private with `publishAt`, so YouTube itself publishes
at the exact slot time. Rendering runs ahead and keeps `buffer_days` filled, so one failed
render never creates a gap: the next run simply fills the slot."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import ledger
from .config import Channel, Config


def upcoming_slots(ch: Channel, tz: str, start: datetime, days: int) -> list[datetime]:
    zone = ZoneInfo(tz)
    local_start = start.astimezone(zone)
    out = []
    for d in range(days + 1):
        day: date = (local_start + timedelta(days=d)).date()
        for s in ch.slots:
            hh, mm = map(int, s.split(":"))
            out.append(datetime.combine(day, time(hh, mm), tzinfo=zone).astimezone(timezone.utc))
    end = start + timedelta(days=days)
    return sorted(x for x in out if start <= x <= end)


def open_slots(cfg: Config, rows: list[dict], now: datetime) -> list[tuple[datetime, Channel]]:
    """Every unfilled slot inside each channel's buffer window, earliest first."""
    res = []
    for ch in cfg.enabled:
        filled = ledger.filled_slots(rows, ch.id)
        earliest = now + timedelta(minutes=ch.lead_minutes)
        for slot in upcoming_slots(ch, cfg.timezone, earliest, ch.buffer_days):
            if ledger.iso(slot) not in filled:
                res.append((slot, ch))
    res.sort(key=lambda x: (x[0], x[1].id))
    return res


def pick_theme(ch: Channel, rows: list[dict]) -> str:
    """Rotate themes by weight: choose the theme that is most behind its share of the
    channel's last 30 scheduled Shorts; ties go to the theme that posted least recently."""
    recent = [r for r in rows if r["channel"] == ch.id and r["status"] == "scheduled"]
    recent.sort(key=lambda r: r["slot_utc"])
    recent = recent[-30:]
    total_w = sum(t.weight for t in ch.themes)
    n = len(recent) + 1

    def deficit(t):
        have = sum(1 for r in recent if r["theme"] == t.id)
        return have - n * t.weight / total_w

    def last_pos(t):
        for i in range(len(recent) - 1, -1, -1):
            if recent[i]["theme"] == t.id:
                return i
        return -1

    return min(ch.themes, key=lambda t: (round(deficit(t), 6), last_pos(t))).id


def pick_style(ch: Channel, theme_id: str, rows: list[dict]) -> str:
    styles = ch.theme(theme_id).styles
    used = [r["style"] for r in rows if r["channel"] == ch.id and r["theme"] == theme_id and r["status"] == "scheduled"]
    return styles[len(used) % len(styles)]


def plan(cfg: Config, rows: list[dict], now: datetime | None = None, channel: str | None = None) -> dict | None:
    now = now or ledger.now_utc()
    slots = open_slots(cfg, rows, now)
    if channel:
        slots = [s for s in slots if s[1].id == channel]
    if not slots:
        return None
    slot, ch = slots[0]
    theme = pick_theme(ch, rows)
    return {"channel": ch.id, "slot_utc": ledger.iso(slot), "theme": theme,
            "style": pick_style(ch, theme, rows), "open_slots": len(slots)}


def cron_lines(cfg: Config, minutes_before: int = 10) -> list[str]:
    """UTC cron expressions that fire `minutes_before` each slot (for the Instagram poster).
    Uses a fixed winter offset for the configured zone; Asia/Kolkata has no DST."""
    zone = ZoneInfo(cfg.timezone)
    ref = datetime(2026, 1, 15, tzinfo=zone)
    out = set()
    for ch in cfg.enabled:
        for s in ch.slots:
            hh, mm = map(int, s.split(":"))
            t = ref.replace(hour=hh, minute=mm) - timedelta(minutes=minutes_before)
            u = t.astimezone(ZoneInfo("UTC"))
            out.add((u.hour, u.minute))
    return [f"{m} {h} * * *" for h, m in sorted(out)]
