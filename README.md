# Multi-channel stickman Shorts

Writes, narrates, animates and posts stickman Shorts to YouTube and Instagram for four channels,
on a fixed daily schedule, with no paid services. Inspired by the `things._.explained` bot, and kept
completely separate from it.

| Channel (names are placeholders) | Themes | Posts (IST) |
|---|---|---|
| `deep-stories` | Philosophy, Mythology and religious stories (alternating) | 07:30, 19:00 |
| `level-up` | Fitness, motivation and money; Technology (alternating) | 06:30, 18:00 |
| `vs` | Absurd "X vs Y" matchups, decided by real facts | 08:30, 20:30 |
| `genrec-tech` | How AI agents, automation and software really work; 1 in 4 Shorts features a GenrecAI product | 12:30, 21:30 |

Two Shorts a day per channel. That's steady enough for the Shorts feed without looking like spam,
and the gap between posts is never longer than about 13 hours. Each slot is timed for an Indian
evening or morning that is also a US evening or morning. Edit the times in `channels.yaml`.

## How it works

```
produce.yml (every 2 h)                         publish.yml (10 min before each slot)
  plan: earliest unfilled slot, 2 days ahead       Instagram: post the Reel when its slot arrives
  topic  -> channels/<channel>/topics-<theme>.csv  (YouTube needs nothing: it publishes itself)
  script -> writer + critic loop, editorial standard, content policy
  voice  -> free Edge neural voice + exact word timings
  render -> OpenCode agent + stickman-video-director Skill (B7 Pixel Theater / A5 Historical Cartoon)
  QA     -> size, length, audio, scene changes, black frames
  upload -> YouTube as private with publishAt = the slot; MP4 kept for Instagram
```

Here's what changed from the original bot, and why:

- **No gaps when a render fails.** About 1 in 3 of the old bot's runs failed. Here each Short goes to
  YouTube ahead of time with `publishAt`, so YouTube publishes it at the exact minute, and the buffer
  stays 2 days ahead. When a render fails, the next run fills that slot. Late cron runs don't matter.
- **The agent only draws.** The script and narration are made before the render agent starts. The
  agent gets a finished script, the narration audio and word timings, so it has far less to get wrong.
- **Scripts have to pass the editorial standard.** A critic scores every draft on the 7 tests (3-second
  hook, curiosity by 10 seconds, something new every 10-15 seconds, a midpoint turn, escalation, a
  payoff, rewatch value) and on the stranger test. A draft is rewritten up to 3 times, and a script
  that never passes is never made into a video. Hard rules also block calls to action, banned openers,
  regional slang and depicting the Prophet Muhammad. See `prompts/editorial-standard.md` and
  `prompts/content-policy.md`.
- **Plain Python modules, not 1,000 lines of YAML.** The logic is in `pipeline/` and covered by
  `tests/`.
- **Topic banks refill themselves.** When a theme has fewer than 8 topics left, the model proposes
  new ones and near-duplicates are dropped.

## The GenrecAI channel

`genrec-tech` is a normal value-first tech channel that also promotes GenrecAI's own products
(Tabble, LuminaIQ, SelectStar, HailBrowser) without turning into an ad feed:

- **3 in 4 Shorts are pure value.** Every 4th Short picks a topic tagged with a product in
  `channels/genrec-tech/topics-ai-builds.csv`, and the product is the worked example of a bigger idea.
- **Hard rules on product Shorts:** named at most twice, never in the hook or title, honestly framed as
  "we built", no URL, price or call to action in the voice-over. The script may only use the product facts
  listed in `channels.yaml` under `promo`, and the critic fails any Short that reads like an advert.
- **The description does the selling.** Product Shorts add the product link, and every Short ends with a
  one-line GenrecAI footer linking genrecai.com.
- Edit products, facts or the 1-in-4 ratio (`promo.every`) in `channels.yaml`.

## Setup (needs your accounts)

1. **Make this repo public**, or GitHub Actions minutes run out. Each Short takes about 45-90 minutes
   of runner time, and 6 a day is far over a private repo's 2,000 free minutes a month. Secrets stay
   secret in a public repo.
2. **YouTube**, for each channel:
   - Create the channel (one Google account can own several brand channels).
   - Use one Google Cloud project with the YouTube Data API v3 enabled, and an OAuth client of type
     "Desktop". Its quota is 10,000 units a day and one upload costs 1,600, so one project covers 6
     uploads a day, which covers the first three channels. The GenrecAI channel needs a **second** Cloud
     project, with secrets `GENREC_YT_CLIENT_ID`, `GENREC_YT_CLIENT_SECRET` and `GENREC_YT_REFRESH_TOKEN`. Don't reuse the `things._.explained` project, or
     the two bots share that quota.
   - **Until the project passes YouTube's API audit, every API upload stays private.** Apply at
     https://support.google.com/youtube/contact/yt_api_form . Videos still get uploaded and scheduled
     meanwhile; you can make them public by hand.
   - Get a refresh token for each channel with `python scripts/get_youtube_token.py --client-id ... --client-secret ...`
     (pick that channel when Google asks), then add these repo secrets:
     `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `DEEP_YT_REFRESH_TOKEN`, `LEVELUP_YT_REFRESH_TOKEN`, `VS_YT_REFRESH_TOKEN`
     (plus the three `GENREC_YT_*` secrets above).
3. **Instagram (optional, per channel):** use a Professional account linked to a Facebook Page, and a
   long-lived token with `instagram_basic, instagram_content_publish, pages_show_list,
   pages_read_engagement`. Add the secret `DEEP_IG_ACCESS_TOKEN` (and the same for `LEVELUP_`,
   `VS_` and `GENREC_`), and put the handle in `channels.yaml` under `instagram.username`. Without these, that
   channel posts to YouTube only.
4. **Optional, a better model:** set `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` for any
   OpenAI-compatible endpoint (a Gemini, Groq or OpenRouter free tier). Without them, the free
   OpenCode DeepSeek model is used, the same one the original bot uses.
5. In Actions, run **produce** once with `no_upload` ticked to check a render, then let the schedule run.

## Everyday use

```bash
python -m pipeline status          # buffer and topics left per channel
python -m pipeline plan            # what the next run will make
python -m pipeline refill-topics   # top up the topic banks now
python -m pipeline sync-cron       # run after changing any slot time
python -m pytest -q tests
```

- **Add a topic:** add a row to `channels/<channel>/topics-<theme>.csv` with status `pending`.
- **Pause a channel:** set `enabled: false`.
- **Rename a channel:** change `name`. It's only used in prompts.
- `state/ledger.csv` records every Short, its slot, its YouTube and Instagram links, and every failure.

## Content rules (summary)

VS matchups never use living people or politicians, and never compare anyone to Hitler. Living-faith
figures only appear in respectful "what each tradition teaches" comparisons, never as fighters. Ancient
pantheons (Greek, Norse, Egyptian) can clash the way they do in their own myths. There's no medical,
legal or personal financial advice, and no get-rich claims. The full rules are in
`prompts/content-policy.md`.
