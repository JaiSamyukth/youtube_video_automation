"""Command line entry: python -m pipeline <command>

  plan            print which channel/slot/theme the next video is for (or that the buffer is full)
  produce         make one Short end to end and schedule it: topic -> script -> voice -> render -> QA -> upload
  publish-due     post Instagram Reels whose slot time has arrived
  refill-topics   top up any topic bank that is running low (LLM proposes, duplicates dropped)
  status          show the buffer per channel
  sync-cron       rewrite publish.yml's cron lines from the slots in channels.yaml
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from datetime import timedelta
from pathlib import Path

from . import config, ledger, llm, schedule, topics
from .config import ROOT

LOW_WATER = 8   # refill a topic bank when fewer than this many topics are pending


def log(*a):
    print(*a, flush=True)


def run_url() -> str:
    s, r, i = os.environ.get("GITHUB_SERVER_URL"), os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_RUN_ID")
    return f"{s}/{r}/actions/runs/{i}" if s and r and i else "local"


def cmd_plan(cfg, args):
    p = schedule.plan(cfg, ledger.read(), channel=args.channel)
    print(json.dumps(p or {"skip": True, "reason": "every channel's buffer is full"}))
    if args.github_output and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"skip={'false' if p else 'true'}\n")
            if p:
                f.write(f"style={p['style']}\nchannel={p['channel']}\n")


def promo_due(ch, rows: list[dict]) -> bool:
    """True when this channel's next Short should feature a product: one in every `promo.every` Shorts."""
    if not ch.promo or not ch.promo.get("products"):
        return False
    made = sorted((r for r in rows if r["channel"] == ch.id and r["status"] in ("scheduled", "published", "rendered")),
                  key=lambda r: r["slot_utc"])
    recent = made[-(ch.promo["every"] - 1):]
    return len(recent) == ch.promo["every"] - 1 and not any(r["notes"].startswith("promo:") for r in recent)


def _metadata(script: dict, ch=None, product: str = "") -> dict:
    tags = []
    for t in script.get("hashtags", []):
        t = re.sub(r"[^a-z0-9]", "", str(t).lower())
        if t and t != "shorts" and t not in tags:
            tags.append(t)
    desc = re.sub(r"[ \t]*#\w+", "", str(script.get("description", ""))).strip()
    if ch is not None and ch.promo:
        prod = ch.promo["products"].get(product)
        if prod and prod["url"]:
            desc += f"\n\nIn this Short: {product}, which we built: {prod['url']}"
        if ch.promo.get("footer"):
            desc += "\n\n" + ch.promo["footer"]
    return {"title": str(script["title"]).strip()[:100], "description": desc, "hashtags": ["shorts"] + tags[:4]}


def cmd_produce(cfg, args):
    from . import qa, render, script as scriptmod, storage, tts, youtube
    rows = ledger.read()
    p = schedule.plan(cfg, rows, channel=args.channel)
    if not p:
        log("buffer full for every channel; nothing to make")
        return 0
    ch = cfg.channel(p["channel"])
    theme = ch.theme(p["theme"])
    style = args.style or p["style"]
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    log(f"making a {theme.id} Short for {ch.id}, slot {p['slot_utc']}, style {style}")

    bank = ch.topics_path(theme.id)
    tried: set[str] = set()
    result = topic = None
    want = promo_due(ch, rows) if ch.promo else None
    if want:
        log("this Short features one of the channel's own products")
    for _ in range(2):                         # a topic whose script never passes is set aside; try one more
        topic = (topics.next_pending(bank, tried, want) if want is not None else None) \
            or topics.next_pending(bank, tried, False if ch.promo else None) or topics.next_pending(bank, tried)
        if not topic:
            raise SystemExit(f"topic bank {bank} is empty; run refill-topics")
        tried.add(topic["id"])
        try:
            result = scriptmod.write_script(ch, theme, topic, cfg.llm_model, log=log)
            break
        except Exception as e:
            log(f"script for {topic['id']} failed: {e}")
            topics.mark(bank, topic["id"], "failed", f"script: {str(e)[:200]}")
    if not result:
        raise SystemExit("no script passed the editorial bar this run")
    s = result["script"]
    (work / "script.json").write_text(json.dumps(s, indent=1, ensure_ascii=False), encoding="utf-8")
    (work / "review.json").write_text(json.dumps(result["review"], indent=1), encoding="utf-8")

    voice = tts.narrate(s["beats"], ch.voice, ch.voice_rate, work / "narration.wav", log=log)
    tts.write_timing(work / "timing.json", voice, s["beats"])
    log(f"narration {voice['duration']:.1f}s with {ch.voice}")
    if voice["duration"] > 58:
        topics.mark(bank, topic["id"], "failed", "narration over 58s")
        raise SystemExit("narration is over 58 seconds")

    row_id = f"{ch.id}-{p['slot_utc'][:16].replace(':', '').replace('-', '')}"
    base = {"id": row_id, "channel": ch.id, "theme": theme.id, "topic_id": topic["id"], "topic": topic["topic"],
            "style": style, "slot_utc": p["slot_utc"], "created_utc": ledger.iso(ledger.now_utc()), "run_url": run_url()}
    promo_note = f"promo:{topic['product']} " if topic.get("product") and ch.promo else ""
    try:
        video = render.render(work, Path(args.skill_dir), style, voice["duration"], cfg.llm_model, log=log)
        report = qa.check(video, voice["duration"])
        (work / "out" / "qa.json").write_text(json.dumps(report, indent=1))
        log(f"QA: {report}")
        if not report["ok"]:
            raise RuntimeError("QA failed: " + "; ".join(report["problems"]))
    except Exception as e:
        topics.mark(bank, topic["id"], "failed", f"render: {str(e)[:200]}")
        ledger.upsert({**base, "status": "failed", "notes": str(e)[:300]})
        raise

    meta = _metadata(s, ch, topic.get("product", ""))
    (work / "out" / "metadata.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    if args.no_upload:
        ledger.upsert({**base, "status": "rendered", "title": meta["title"], "notes": promo_note + "no-upload test run"})
        log(f"rendered (not uploaded): {video}")
        return 0
    # the slot may have passed while we rendered: move to the next open slot of this channel
    slot = p["slot_utc"]
    if ledger.parse_iso(slot) < ledger.now_utc() + timedelta(minutes=20):
        nxt = schedule.plan(cfg, ledger.read(), channel=ch.id)
        if not nxt:
            raise SystemExit("no open slot left for this channel")
        slot = nxt["slot_utc"]
        row_id = f"{ch.id}-{slot[:16].replace(':', '').replace('-', '')}"
        base.update(slot_utc=slot, id=row_id)
    yt = youtube.upload(ch, str(video), meta, slot)
    log(f"YouTube: {yt}")
    asset = f"{row_id}.mp4"
    ig_status = "pending"
    if ch.instagram_username and ch.secret("IG", "ACCESS_TOKEN"):
        storage.put(video, asset)
    else:
        asset, ig_status = "", "skipped: instagram not configured"
    meta_store = ROOT / "state" / "meta" / f"{row_id}.json"
    meta_store.parent.mkdir(parents=True, exist_ok=True)
    meta_store.write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    ledger.upsert({**base, "status": "scheduled", "yt_id": yt["id"], "yt_url": yt["url"], "ig_status": ig_status,
                   "asset": asset, "title": meta["title"], "notes": promo_note.strip()})
    topics.mark(bank, topic["id"], "done", f"{yt['url']} for {slot}")
    log(f"scheduled {yt['url']} for {slot}")
    return 0


def cmd_publish_due(cfg, args):
    from . import instagram, storage
    now = ledger.now_utc()
    rows = ledger.read()
    due = [r for r in rows if r["status"] == "scheduled" and r["ig_status"] == "pending" and r["asset"]
           and ledger.parse_iso(r["slot_utc"]) <= now + timedelta(minutes=args.early_minutes)]
    fails = 0
    for r in sorted(due, key=lambda r: r["slot_utc"]):
        late = now - ledger.parse_iso(r["slot_utc"])
        if late > timedelta(hours=6):
            r["ig_status"] = "skipped: missed slot by over 6h"   # posting much later would bunch posts
            ledger.upsert(r)
            continue
        wait = (ledger.parse_iso(r["slot_utc"]) - ledger.now_utc()).total_seconds()
        if wait > 0:
            import time
            log(f"waiting {int(wait)}s for {r['id']}'s slot")
            time.sleep(wait)
        ch = cfg.channel(r["channel"])
        try:
            meta = json.loads((ROOT / "state" / "meta" / f"{r['id']}.json").read_text())
            path = storage.get(r["asset"], Path(args.work))
            res = instagram.publish(ch, str(path), meta)
            r.update(ig_status="published", ig_id=res["id"], ig_url=res["url"])
            storage.delete(r["asset"])
            log(f"Instagram published {r['id']}: {res['url']}")
        except Exception as e:
            fails += 1
            r["ig_status"] = "pending" if "processing" in str(e).lower() else f"failed: {str(e)[:150]}"
            log(f"Instagram failed for {r['id']}: {e}")
        ledger.upsert(r)
    return 1 if fails else 0


def _promo_note(ch) -> str:
    if not ch.promo:
        return ""
    lines = "\n".join(f"- {n}: {'; '.join(p['facts'])}" for n, p in ch.promo["products"].items())
    return ("This channel is run by the makers of these products:\n" + lines + "\n"
            f"About 1 in {ch.promo['every']} topics should be one where a product is the honest, natural example of a\n"
            "bigger idea (set \"product\" to its exact name). Every other topic leaves \"product\" empty. A product\n"
            "topic must still be a topic people would watch if the product did not exist.")


def cmd_refill(cfg, args):
    for ch in cfg.enabled:
        for t in ch.themes:
            bank = ch.topics_path(t.id)
            if topics.pending_count(bank) >= LOW_WATER and not args.force:
                continue
            existing = "\n".join(f"- {r['topic']}" for r in topics.read(bank)) or "(none yet)"
            guide = t.prompt_path.read_text(encoding="utf-8") if t.prompt_path.exists() else ""
            prompt = (ROOT / "prompts" / "topic-ideas.md").read_text(encoding="utf-8").format(
                channel_name=ch.name, theme_id=t.id, tone=t.tone, theme_guide=guide, existing=existing,
                count=args.count, promo_note=_promo_note(ch), content_policy=(ROOT / "prompts" / "content-policy.md").read_text(encoding="utf-8"))
            try:
                items = llm.ask_json(prompt, model=cfg.llm_model).get("topics", [])
            except Exception as e:
                log(f"refill {ch.id}/{t.id} failed: {e}")
                continue
            n = topics.add(bank, t.id[:3], items, set(ch.promo.get("products", {})) if ch.promo else set())
            log(f"{ch.id}/{t.id}: added {n} topics")
    return 0


def cmd_status(cfg, args):
    rows = ledger.read()
    now = ledger.now_utc()
    for ch in cfg.channels:
        up = sorted(r["slot_utc"] for r in rows if r["channel"] == ch.id and r["status"] == "scheduled"
                    and ledger.parse_iso(r["slot_utc"]) > now)
        banks = ", ".join(f"{t.id}: {topics.pending_count(ch.topics_path(t.id))} topics left" for t in ch.themes)
        log(f"{ch.id} ({'on' if ch.enabled else 'off'}): {len(up)} scheduled ahead {up[:4]} | {banks}")
    return 0


def cmd_sync_cron(cfg, args):
    path = ROOT / ".github" / "workflows" / "publish.yml"
    s = path.read_text(encoding="utf-8")
    lines = "\n".join(f"    - cron: '{c}'" for c in schedule.cron_lines(cfg))
    new = re.sub(r"(  schedule:\n)(?:    - cron: '[^']*'\n)+", lambda m: m.group(1) + lines + "\n", s)
    path.write_text(new, encoding="utf-8")
    log(lines)
    return 0


def cmd_merge_state(cfg, args):
    """After `git pull`, fold this run's copies (saved in --from) back into the fresh files."""
    src = Path(args.src)
    mine = ledger.read(src / "ledger.csv")
    ledger.write(ledger.merge(ledger.read(), mine))
    for f in src.glob("topics__*.csv"):
        ch_id, name = f.stem.split("__")[1:3]
        dst = ROOT / "channels" / ch_id / f"{name}.csv"
        topics.write(dst, topics.merge(topics.read(dst), topics.read(f)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("plan"); a.add_argument("--channel"); a.add_argument("--github-output", action="store_true")
    a = sub.add_parser("produce")
    a.add_argument("--channel"); a.add_argument("--style"); a.add_argument("--work", default="work")
    a.add_argument("--skill-dir", default=os.environ.get("SKILL_DIR", "svd/skills/directing-stickman-videos"))
    a.add_argument("--no-upload", action="store_true")
    a = sub.add_parser("publish-due"); a.add_argument("--work", default="work-ig"); a.add_argument("--early-minutes", type=int, default=15)
    a = sub.add_parser("refill-topics"); a.add_argument("--count", type=int, default=15); a.add_argument("--force", action="store_true")
    sub.add_parser("status")
    sub.add_parser("sync-cron")
    a = sub.add_parser("merge-state"); a.add_argument("--from", dest="src", required=True)
    args = ap.parse_args(argv)
    cfg = config.load()
    fn = {"plan": cmd_plan, "produce": cmd_produce, "publish-due": cmd_publish_due, "refill-topics": cmd_refill,
          "status": cmd_status, "sync-cron": cmd_sync_cron, "merge-state": cmd_merge_state}[args.cmd]
    try:
        return fn(cfg, args) or 0
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
