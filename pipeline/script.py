"""Write a script, check it with hard rules, have a critic score it against the editorial
standard, and revise until it passes (or give up so a weak Short is never published)."""
from __future__ import annotations

import json
import re

from . import llm
from .config import ROOT, Channel, Theme

PROMPTS = ROOT / "prompts"
PASS_MIN = 7          # every editorial test must score at least this
STRANGER_MIN = 7      # and the stranger test at least this (7 = genuinely good on the critic's scale)
MAX_DRAFTS = 4

BANNED_OPENERS = ("did you know", "in this video", "let me tell you", "here's why", "here is why",
                  "today we", "have you ever wondered", "hey guys", "welcome")
BANNED_ANYWHERE = ("subscribe", "follow for more", "thanks for watching", "like and share",
                   "smash that", "comment below")
REGIONAL_SLANG = re.compile(r"\b(da|di|machan|macha|seri|venuma|yaar|bhai|na\?)\b", re.I)
# Never depicted, in any channel (content policy). Checked against the visual descriptions.
NEVER_DEPICT = re.compile(r"\b(muhammad|mohammed|mohammad|prophet of islam)\b", re.I)


def _read(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def narration(script: dict) -> str:
    return " ".join(b.get("narration", "").strip() for b in script.get("beats", [])).strip()


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", text))


def hard_checks(script: dict, ch: Channel) -> list[str]:
    """Deterministic rules. Any problem returned here means the draft cannot ship."""
    errs = []
    beats = script.get("beats")
    if not isinstance(beats, list) or not (6 <= len(beats) <= 12):
        return ["beats must be a list of 6-12 beats"]
    for i, b in enumerate(beats):
        for k in ("narration", "visual", "backdrop"):
            if not str(b.get(k, "")).strip():
                errs.append(f"beat {i + 1} is missing {k}")
        if word_count(b.get("narration", "")) > 30:
            errs.append(f"beat {i + 1} is too long for one shot (over 30 words); split it")
    text = narration(script)
    n = word_count(text)
    if not (ch.words_min <= n <= ch.words_max + 10):   # small slack; the real limit is the measured audio (<58 s)
        errs.append(f"narration is {n} words; it must be {ch.words_min}-{ch.words_max}")
    first = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0] if text else ""
    if word_count(first) > 16:
        errs.append(f"the opening sentence is {word_count(first)} words; the hook must be at most 14")
    low = text.lower()
    if low.startswith(BANNED_OPENERS):
        errs.append("banned opener; start with a concrete surprising situation or claim")
    for p in BANNED_ANYWHERE:
        if p in low:
            errs.append(f"remove the call-to-action phrase '{p}'")
    if REGIONAL_SLANG.search(text):
        errs.append("regional slang found; use plain international English")
    visuals = " ".join(str(b.get("visual", "")) + " " + str(b.get("backdrop", "")) for b in beats)
    if NEVER_DEPICT.search(visuals):
        errs.append("a visual depicts the Prophet Muhammad; content policy forbids any depiction")
    title = str(script.get("title", ""))
    if not title or len(title) > 70:
        errs.append("title missing or over 70 characters")
    if not str(script.get("description", "")).strip():
        errs.append("description missing")
    return errs


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def critic_passes(review: dict) -> bool:
    s = review.get("scores") or {}
    keys = ["hook_3s", "curiosity_10s", "new_info_every_15s", "midpoint_change", "escalation", "payoff", "rewatch"]
    try:
        ok = all(float(s.get(k, 0)) >= PASS_MIN for k in keys) and float(s.get("stranger_test", 0)) >= STRANGER_MIN
    except (TypeError, ValueError):
        return False
    return ok and bool(review.get("policy_ok")) and not review.get("factual_doubts")


def score(review: dict) -> float:
    s = review.get("scores") or {}
    try:
        return sum(float(v) for v in s.values()) + (0 if review.get("policy_ok") else -100)
    except (TypeError, ValueError):
        return -1000


def writer_prompt(ch: Channel, theme: Theme, topic: dict, revision_notes: str = "") -> str:
    guide = theme.prompt_path.read_text(encoding="utf-8") if theme.prompt_path.exists() else ""
    return _read("script-writer.md").format(
        channel_name=ch.name, theme_id=theme.id, tone=theme.tone, topic=topic["topic"],
        angle=topic.get("angle", ""), theme_guide=guide, editorial_standard=_read("editorial-standard.md"),
        content_policy=_read("content-policy.md"), words_min=ch.words_min, words_max=ch.words_max,
        revision_notes=("\nREVISION: your previous draft failed review. Fix every point below and return the "
                        "full revised JSON.\n" + revision_notes) if revision_notes else "")


def critic_prompt(ch: Channel, theme: Theme, script: dict) -> str:
    return _read("script-critic.md").format(
        channel_name=ch.name, theme_id=theme.id, editorial_standard=_read("editorial-standard.md"),
        content_policy=_read("content-policy.md"), script_json=json.dumps(script, indent=1, ensure_ascii=False))


def write_script(ch: Channel, theme: Theme, topic: dict, model: str, log=print) -> dict:
    """Returns {"script": ..., "review": ..., "drafts": n}. Raises if no draft passes."""
    notes, best = "", None
    for draft in range(1, MAX_DRAFTS + 1):
        script = llm.ask_json(writer_prompt(ch, theme, topic, notes), model=model)
        errs = hard_checks(script, ch)
        if errs:
            log(f"draft {draft}: hard rule failures: {errs}")
            notes = "\n".join(f"- {e}" for e in errs)
            continue
        review = llm.ask_json(critic_prompt(ch, theme, script), model=model)
        log(f"draft {draft}: scores {review.get('scores')} policy_ok={review.get('policy_ok')} verdict={review.get('verdict')}")
        if best is None or score(review) > score(best["review"]):
            best = {"script": script, "review": review, "drafts": draft}
        if critic_passes(review):
            return {"script": script, "review": review, "drafts": draft}
        script, review = best["script"], best["review"]   # always revise the strongest draft so far
        weak = [k for k, v in (review.get("scores") or {}).items() if _num(v) < (STRANGER_MIN if k == "stranger_test" else PASS_MIN)]
        notes = ("Weakest tests: " + ", ".join(weak) + "\n" if weak else "") + "\n".join(
            f"- {x}" for x in (review.get("fixes") or []) + (review.get("policy_issues") or [])
            + [f"doubtful claim (cut it or make it accurate): {d}" for d in (review.get("factual_doubts") or [])])
        notes += "\nKeep everything that already works. Your previous draft:\n" + json.dumps(script, ensure_ascii=False)
    raise RuntimeError(f"no draft passed the editorial bar after {MAX_DRAFTS} drafts; best was "
                       f"{best['review'].get('scores') if best else 'none'}")
