"""Load channels.yaml into small dataclasses."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "channels.yaml"


@dataclass
class Theme:
    id: str
    weight: float
    styles: list[str]
    tone: str = ""

    @property
    def prompt_path(self) -> Path:
        return ROOT / "prompts" / "themes" / f"{self.id}.md"


@dataclass
class Channel:
    id: str
    name: str
    enabled: bool
    slots: list[str]
    themes: list[Theme]
    voice: str
    voice_rate: str
    youtube_prefix: str
    instagram_prefix: str
    instagram_username: str
    buffer_days: int
    lead_minutes: int
    words_min: int
    words_max: int

    def theme(self, theme_id: str) -> Theme:
        for t in self.themes:
            if t.id == theme_id:
                return t
        raise KeyError(f"{self.id} has no theme {theme_id}")

    def topics_path(self, theme_id: str) -> Path:
        return ROOT / "channels" / self.id / f"topics-{theme_id}.csv"

    def secret(self, platform: str, name: str) -> str:
        """Read e.g. DEEP_YT_REFRESH_TOKEN; shared YT_CLIENT_ID/SECRET may be unprefixed."""
        prefix = self.youtube_prefix if platform == "YT" else self.instagram_prefix
        return (os.environ.get(f"{prefix}_{platform}_{name}") or os.environ.get(f"{platform}_{name}") or "").strip()


@dataclass
class Config:
    timezone: str
    skill_commit: str
    llm_provider: str
    llm_model: str
    channels: list[Channel] = field(default_factory=list)

    def channel(self, cid: str) -> Channel:
        for c in self.channels:
            if c.id == cid:
                return c
        raise KeyError(f"no channel {cid}")

    @property
    def enabled(self) -> list[Channel]:
        return [c for c in self.channels if c.enabled]


def load(path: Path | str = CONFIG_PATH) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    d = raw.get("defaults", {})
    chans = []
    seen = set()
    for c in raw["channels"]:
        if c["id"] in seen:
            raise ValueError(f"duplicate channel id {c['id']}")
        seen.add(c["id"])
        for s in c["slots"]:
            hh, mm = s.split(":")
            assert 0 <= int(hh) < 24 and 0 <= int(mm) < 60, f"bad slot {s}"
        chans.append(Channel(
            id=c["id"], name=c.get("name", c["id"]), enabled=bool(c.get("enabled", True)),
            slots=sorted(c["slots"]),
            themes=[Theme(t["id"], float(t.get("weight", 1)), list(t.get("styles", ["B7"])), t.get("tone", ""))
                    for t in c["themes"]],
            voice=c.get("voice", d.get("voice", "en-US-AndrewMultilingualNeural")),
            voice_rate=c.get("voice_rate", d.get("voice_rate", "+10%")),
            youtube_prefix=c.get("youtube", {}).get("secret_prefix", c["id"].upper().replace("-", "")),
            instagram_prefix=c.get("instagram", {}).get("secret_prefix", c["id"].upper().replace("-", "")),
            instagram_username=c.get("instagram", {}).get("username", "") or "",
            buffer_days=int(c.get("buffer_days", d.get("buffer_days", 2))),
            lead_minutes=int(c.get("lead_minutes", d.get("lead_minutes", 45))),
            words_min=int(c.get("words_min", d.get("words_min", 110))),
            words_max=int(c.get("words_max", d.get("words_max", 150))),
        ))
    llm = raw.get("llm", {})
    return Config(timezone=raw.get("timezone", "Asia/Kolkata"), skill_commit=str(raw.get("skill_commit", "")),
                  llm_provider=llm.get("provider", "opencode"),
                  llm_model=llm.get("model", "opencode/deepseek-v4.1-flash-free"), channels=chans)
