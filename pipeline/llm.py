"""Ask a language model for JSON. Two routes:
- opencode: the free OpenCode CLI model (same route as the original bot, no key needed)
- openai_compat: any OpenAI-compatible endpoint (LLM_BASE_URL, LLM_API_KEY, LLM_MODEL),
  e.g. a Gemini, Groq or OpenRouter free tier. Used automatically when LLM_BASE_URL is set.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

OPENCODE_PERMS = '{"permission":{"external_directory":"allow","bash":"allow","edit":"allow","webfetch":"deny"}}'


class LLMError(RuntimeError):
    pass


def extract_json(text: str):
    """Pull the first JSON object/array out of model output (tolerates ``` fences and chatter)."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start < 0:
            continue
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    raise LLMError("no JSON found in model output")


def _opencode(prompt: str, model: str, timeout: int) -> str:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "answer.json"
        full = (prompt + f"\n\nWrite ONLY the JSON answer (no prose, no code fences) to the file {out} "
                "using your file-writing tool, then stop. Do not run any other commands.")
        env = {**os.environ, "OPENCODE_CONFIG_CONTENT": OPENCODE_PERMS}
        proc = subprocess.Popen(["opencode", "run", "-m", model, full], cwd=d, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        t0 = time.time()
        while proc.poll() is None:
            if out.exists() and out.stat().st_size > 2:
                time.sleep(3)  # let the write finish
                break
            if time.time() - t0 > timeout:
                break
            time.sleep(2)
        if proc.poll() is None:
            proc.kill()
        stdout = proc.stdout.read() if proc.stdout else ""
        if out.exists() and out.stat().st_size > 2:
            return out.read_text(encoding="utf-8")
        return stdout


def _openai_compat(prompt: str, timeout: int) -> str:
    """LLM_MODEL may list fallbacks, comma-separated: a busy (503) or rate-limited (429) model passes to the next."""
    base = os.environ["LLM_BASE_URL"].rstrip("/")
    models = [m.strip() for m in os.environ.get("LLM_MODEL", "").split(",") if m.strip()] or [""]
    last = None
    for m in models:
        body = {"model": m, "temperature": 0.8,
                "messages": [{"role": "user", "content": prompt + "\n\nAnswer with JSON only."}]}
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + os.environ.get("LLM_API_KEY", "")})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 503, 404):
                raise
    raise LLMError(f"every model in LLM_MODEL failed; last error: {last}")


def ask_json(prompt: str, model: str = "opencode/deepseek-v4.1-flash-free", timeout: int = 420, tries: int = 3):
    last = None
    for attempt in range(tries):
        try:
            if os.environ.get("LLM_BASE_URL"):
                raw = _openai_compat(prompt, timeout)
            else:
                raw = _opencode(prompt, model, timeout)
            return extract_json(raw)
        except Exception as e:  # network blips and malformed answers both get a fresh try
            last = e
            time.sleep(10 * (attempt + 1))
    raise LLMError(f"model gave no usable JSON after {tries} tries: {last}")
