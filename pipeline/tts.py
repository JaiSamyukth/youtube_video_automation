"""Narration with the free Edge neural voices, plus exact per-word timings (from the voice
service's own word boundaries), so captions and beat timing never have to be guessed."""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
from pathlib import Path

# Same voice polish the original bot used: rumble cut, warmth, presence, gentle compression.
VOICE_CHAIN = ("highpass=f=80,equalizer=f=140:t=q:w=1.2:g=3.5,equalizer=f=4500:t=q:w=1.5:g=2.5,"
               "acompressor=threshold=-18dB:ratio=3:attack=5:release=50,alimiter=limit=0.95")


async def _stream(text: str, voice: str, rate: str, mp3: Path) -> list[dict]:
    import edge_tts
    words = []
    comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    with mp3.open("wb") as f:
        async for ch in comm.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                start = ch["offset"] / 1e7
                words.append({"w": ch["text"], "start": round(start, 3), "end": round(start + ch["duration"] / 1e7, 3)})
    return words


def synthesize(text: str, voice: str, rate: str, out_wav: Path, tries: int = 6) -> dict:
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    mp3 = out_wav.with_suffix(".raw.mp3")
    last = None
    for i in range(tries):
        try:
            words = asyncio.run(_stream(text, voice, rate, mp3))
            if mp3.stat().st_size < 4000 or not words:
                raise RuntimeError("empty audio")
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-af", VOICE_CHAIN,
                            "-ar", "48000", "-ac", "1", str(out_wav)], check=True)
            dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                                 "-of", "csv=p=0", str(out_wav)]).strip())
            return {"wav": str(out_wav), "duration": round(dur, 3), "words": words, "voice": voice, "rate": rate}
        except Exception as e:
            last = e
            time.sleep(4 * (i + 1))
    raise RuntimeError(f"edge-tts produced no audio after {tries} tries: {last}")


KOKORO_DIR = Path(os.environ.get("KOKORO_DIR", Path.home() / ".cache" / "kokoro"))
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


def _kokoro_files() -> tuple[Path, Path]:
    KOKORO_DIR.mkdir(parents=True, exist_ok=True)
    m, v = KOKORO_DIR / "kokoro-v1.0.onnx", KOKORO_DIR / "voices-v1.0.bin"
    for f in (m, v):
        if not f.exists() or f.stat().st_size < 1_000_000:
            subprocess.run(["curl", "-fsSL", "-o", str(f), KOKORO_URL + f.name], check=True)
    return m, v


def synthesize_kokoro(beats: list[dict], out_wav: Path, voice: str = "am_michael") -> dict:
    """Offline fallback voice (Kokoro, Apache-2.0). Synthesised beat by beat; word times inside a
    beat are spread by word length, which is close enough for 2-4 word captions."""
    import numpy as np
    import soundfile as sf
    from kokoro_onnx import Kokoro
    m, v = _kokoro_files()
    k = Kokoro(str(m), str(v))
    chunks, words, t, sr = [], [], 0.0, 24000
    for b in beats:
        text = b["narration"].strip()
        audio, sr = k.create(text, voice=voice, speed=1.1, lang="en-us")
        dur = len(audio) / sr
        toks = text.split()
        weights = [len(w) + 2 for w in toks]
        acc = 0.0
        for w, wt in zip(toks, weights):
            span = dur * 0.92 * wt / sum(weights)
            words.append({"w": w, "start": round(t + 0.04 + acc, 3), "end": round(t + 0.04 + acc + span, 3)})
            acc += span
        chunks += [audio, np.zeros(int(0.12 * sr), dtype=audio.dtype)]
        t += dur + 0.12
    raw = Path(out_wav).with_suffix(".raw.wav")
    sf.write(raw, np.concatenate(chunks), sr)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-af", VOICE_CHAIN, "-ar", "48000", "-ac", "1",
                    str(out_wav)], check=True)
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                         "-of", "csv=p=0", str(out_wav)]).strip())
    return {"wav": str(out_wav), "duration": round(dur, 3), "words": words, "voice": f"kokoro:{voice}", "rate": "1.1"}


def narrate(beats: list[dict], voice: str, rate: str, out_wav: Path, log=print) -> dict:
    """Channel voice (Edge) first; the offline Kokoro voice if Edge is down or TTS_ENGINE=kokoro."""
    text = " ".join(b["narration"].strip() for b in beats)
    if os.environ.get("TTS_ENGINE", "edge") != "kokoro":
        try:
            return synthesize(text, voice, rate, out_wav)
        except Exception as e:
            log(f"Edge voice failed ({e}); using the offline Kokoro voice")
    return synthesize_kokoro(beats, out_wav)


def beat_timings(beats: list[dict], words: list[dict], duration: float) -> list[dict]:
    """Map each beat to [start, end] seconds. The voice service may split numbers or
    contractions differently from us, so beat boundaries are scaled onto its word list."""
    counts = [len(re.findall(r"[A-Za-z0-9']+", b.get("narration", ""))) for b in beats]
    total, W = max(sum(counts), 1), len(words)
    out, cum = [], 0
    for b, n in zip(beats, counts):
        i0 = min(round(cum * W / total), max(W - 1, 0))
        cum += n
        start = words[i0]["start"] if W else 0.0
        out.append({**b, "start": round(start, 3), "end": 0.0})
    for i in range(len(out) - 1):           # beats butt together, no visual gaps
        out[i]["end"] = out[i + 1]["start"]
    if out:
        out[0]["start"] = 0.0
        out[-1]["end"] = round(duration, 3)
    return out


def write_timing(path: Path, tts: dict, beats: list[dict]) -> dict:
    data = {"duration": tts["duration"], "voice": tts["voice"], "words": tts["words"],
            "beats": beat_timings(beats, tts["words"], tts["duration"])}
    Path(path).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return data


if __name__ == "__main__":  # quick manual check: python -m pipeline.tts "text" out.wav
    import sys
    r = synthesize(sys.argv[1], os.environ.get("VOICE", "en-US-AndrewMultilingualNeural"), "+10%", Path(sys.argv[2]))
    print(json.dumps({k: v for k, v in r.items() if k != "words"}), len(r["words"]), "words")
