import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline import config, ledger, llm, schedule, script, topics, tts

CFG = config.load()


def row(ch, slot, theme, status="scheduled", style="B7", **kw):
    return {**{k: "" for k in ledger.FIELDS}, "id": f"{ch}-{slot}", "channel": ch, "slot_utc": slot,
            "theme": theme, "status": status, "style": style, **kw}


def test_config_loads_three_channels_and_all_topic_banks():
    assert [c.id for c in CFG.channels] == ["deep-stories", "level-up", "vs"]
    for ch in CFG.channels:
        for t in ch.themes:
            assert ch.topics_path(t.id).exists(), t.id
            assert t.prompt_path.exists(), t.id
            assert topics.pending_count(ch.topics_path(t.id)) >= 10


def test_slots_are_ist_and_two_per_day():
    ch = CFG.channel("deep-stories")
    start = datetime(2026, 11, 27, 0, 0, tzinfo=timezone.utc)
    slots = schedule.upcoming_slots(ch, CFG.timezone, start, 2)
    # 07:30 IST = 02:00 UTC, 19:00 IST = 13:30 UTC
    assert [ledger.iso(s) for s in slots] == ["2026-11-27T02:00:00Z", "2026-11-27T13:30:00Z", "2026-11-28T02:00:00Z", "2026-11-28T13:30:00Z"]


def test_plan_fills_earliest_open_slot_and_skips_filled():
    now = datetime(2026, 11, 27, 0, 0, tzinfo=timezone.utc)
    p = schedule.plan(CFG, [], now)
    assert p["channel"] == "level-up" and p["slot_utc"] == "2026-11-27T01:00:00Z"  # 06:30 IST
    p2 = schedule.plan(CFG, [row("level-up", "2026-11-27T01:00:00Z", "fitness-money")], now)
    assert p2["slot_utc"] == "2026-11-27T02:00:00Z" and p2["channel"] == "deep-stories"


def test_failed_rows_do_not_fill_a_slot():
    now = datetime(2026, 11, 27, 0, 0, tzinfo=timezone.utc)
    p = schedule.plan(CFG, [row("level-up", "2026-11-27T01:00:00Z", "fitness-money", status="failed")], now)
    assert p["slot_utc"] == "2026-11-27T01:00:00Z"


def test_lead_time_respected():
    now = datetime(2026, 11, 27, 0, 30, tzinfo=timezone.utc)   # 30 min before the 01:00 slot, lead is 45
    assert schedule.plan(CFG, [], now)["slot_utc"] != "2026-11-27T01:00:00Z"


def test_buffer_full_returns_none():
    now = datetime(2026, 11, 27, 0, 0, tzinfo=timezone.utc)
    rows = [row(ch.id, ledger.iso(s), ch.themes[0].id) for s, ch in schedule.open_slots(CFG, [], now)]
    assert schedule.plan(CFG, rows, now) is None


def test_theme_rotation_alternates():
    ch = CFG.channel("deep-stories")
    rows = []
    seq = []
    for i in range(6):
        t = schedule.pick_theme(ch, rows)
        seq.append(t)
        rows.append(row(ch.id, f"2026-11-{10 + i}T02:00:00Z", t))
    assert seq.count("philosophy") == 3 and seq.count("mythology") == 3
    assert all(a != b for a, b in zip(seq, seq[1:]))


def test_style_rotation():
    ch = CFG.channel("deep-stories")
    assert schedule.pick_style(ch, "philosophy", []) == "A5"
    assert schedule.pick_style(ch, "philosophy", [row(ch.id, "x", "philosophy", style="A5")]) == "B7"


def test_cron_lines_ten_minutes_before_each_slot():
    lines = schedule.cron_lines(CFG)
    assert "50 0 * * *" in lines and "20 13 * * *" in lines and len(lines) == 6


def test_publish_yml_cron_in_sync():
    text = (config.ROOT / ".github/workflows/publish.yml").read_text()
    for c in schedule.cron_lines(CFG):
        assert f"- cron: '{c}'" in text


def test_topic_failure_requeues_then_retires(tmp_path):
    p = tmp_path / "t.csv"
    topics.add(p, "x", [{"topic": "A"}, {"topic": "B"}])
    tid = topics.next_pending(p)["id"]
    topics.mark(p, tid, "failed")
    assert topics.next_pending(p)["id"] == tid          # one more chance
    topics.mark(p, tid, "failed")
    assert topics.next_pending(p)["id"] != tid          # retired after 2 failures


def test_topic_add_dedupes(tmp_path):
    p = tmp_path / "t.csv"
    assert topics.add(p, "x", [{"topic": "Why is the sky blue?"}]) == 1
    assert topics.add(p, "x", [{"topic": "why is the sky BLUE"}, {"topic": "New one"}]) == 1


def test_ledger_merge_keeps_most_progressed(tmp_path):
    a = row("vs", "s1", "versus", status="failed", created_utc="1")
    b = row("vs", "s1", "versus", status="scheduled", created_utc="2")
    b2 = dict(b, ig_status="published")
    assert ledger.merge([a], [b])[0]["status"] == "scheduled"
    assert ledger.merge([b2], [b])[0]["ig_status"] == "published"
    assert len(ledger.merge([a], [row("vs", "s2", "versus")])) == 2


def test_extract_json_handles_fences_and_chatter():
    assert llm.extract_json('Sure!\n```json\n{"a": [1, "}"]}\n```') == {"a": [1, "}"]}
    assert llm.extract_json('blah {"b": 2} trailing') == {"b": 2}
    with pytest.raises(llm.LLMError):
        llm.extract_json("no json")


def good_script(words_per_beat=16, beats=8):
    filler = "the old ship sails on through every storm and every calm until the sailors finally ask what remains"
    w = filler.split()
    bs = [{"role": "development", "narration": " ".join((w * 3)[i:i + words_per_beat]).capitalize() + ".",
           "visual": "a stickman rebuilds a ship plank by plank", "backdrop": f"set {i}", "caption_keyword": "ship"}
          for i in range(beats)]
    bs[0]["narration"] = "Every plank of this ship has been replaced, twice."
    return {"beats": bs, "title": "Is it still the same ship?", "description": "A paradox. Which side are you on?"}


def test_hard_checks_accept_a_valid_script():
    ch = CFG.channel("deep-stories")
    s = good_script()
    n = script.word_count(script.narration(s))
    assert ch.words_min <= n <= ch.words_max, n
    assert script.hard_checks(s, ch) == []


@pytest.mark.parametrize("mutate,needle", [
    (lambda s: s["beats"][0].update(narration="Did you know ships are old?"), "banned opener"),
    (lambda s: s["beats"][-1].update(narration=s["beats"][-1]["narration"] + " Subscribe now."), "subscribe"),
    (lambda s: s["beats"][2].update(visual="the Prophet Muhammad speaks"), "Prophet"),
    (lambda s: s["beats"][3].update(narration="Seri da, this ship is old."), "slang"),
    (lambda s: s.update(beats=s["beats"][:3]), "6-12 beats"),
])
def test_hard_checks_reject(mutate, needle):
    s = good_script()
    mutate(s)
    errs = script.hard_checks(s, CFG.channel("deep-stories"))
    assert any(needle.lower() in e.lower() for e in errs), errs


def test_critic_gate():
    ok = {"scores": {k: 8 for k in ["hook_3s", "curiosity_10s", "new_info_every_15s", "midpoint_change",
                                    "escalation", "payoff", "rewatch", "stranger_test"]},
          "policy_ok": True, "factual_doubts": []}
    assert script.critic_passes(ok)
    assert not script.critic_passes({**ok, "policy_ok": False})
    assert not script.critic_passes({**ok, "factual_doubts": ["x"]})
    low = json.loads(json.dumps(ok)); low["scores"]["stranger_test"] = 6
    assert not script.critic_passes(low)


def test_beat_timings_cover_whole_narration():
    beats = [{"narration": "one two three"}, {"narration": "four five"}, {"narration": "six"}]
    words = [{"w": str(i), "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(6)]
    t = tts.beat_timings(beats, words, 3.2)
    assert t[0]["start"] == 0 and t[-1]["end"] == 3.2
    assert t[1]["start"] == 1.5 and t[2]["start"] == 2.5
    assert all(a["end"] == b["start"] for a, b in zip(t, t[1:]))


def test_render_prompt_fills_every_placeholder(tmp_path):
    from pipeline import render
    for style in ("B7", "A5"):
        p = render.build_prompt(tmp_path, Path("/skill"), style, 47.3)
        assert "{" not in p.replace("{work}", "") or "{work}" not in p
        assert "1080x1920" in p and "47.3" in p


def test_writer_and_critic_prompts_format():
    ch = CFG.channel("vs")
    th = ch.themes[0]
    wp = script.writer_prompt(ch, th, {"topic": "T-rex vs a modern tank", "angle": ""})
    assert "T-rex" in wp and "VS MATCHUPS" in wp and "Prophet Muhammad" in wp
    cp = script.critic_prompt(ch, th, good_script())
    assert "stranger_test" in cp


def test_youtube_body_schedules_private():
    from pipeline import youtube
    b = youtube.build_body({"title": "T", "description": "D", "hashtags": ["shorts", "space"]}, "2026-11-28T02:00:00Z")
    assert b["status"] == {"privacyStatus": "private", "publishAt": "2026-11-28T02:00:00Z",
                           "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False}
    assert b["snippet"]["description"].endswith("#shorts #space")


def test_instagram_caption_drops_youtube_tags():
    from pipeline import instagram
    c = instagram.caption({"title": "T", "description": "D", "hashtags": ["shorts", "Myth", "#greek"]})
    assert c.endswith("#myth #greek") and "#shorts" not in c
