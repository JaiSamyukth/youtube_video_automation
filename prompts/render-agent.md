You are producing the visuals for ONE finished vertical Short with the installed stickman Skill at {skill_dir}
(read its SKILL.md, then only the references it routes you to for style {style}). Work non-interactively in CI;
ask no questions. The SCRIPT AND NARRATION ARE FINAL: do not rewrite, re-voice, re-time or shorten them.

Inputs (already made, do not change them):
- {work}/script.json   the approved script: beats with narration, visual, backdrop and caption_keyword
- {work}/narration.wav  the final narration ({duration:.1f} s). Use it as the only voice track.
- {work}/timing.json    exact per-word times ("words": w/start/end) and per-beat start/end seconds ("beats")

Your job: build the picture for those beats in style {style_name}, burn in captions, add a quiet music bed and
light sound effects, and render the final video.

Route for {style}:
{route}

Hard rules (override any Skill wording):
- Output: {work}/out/video.mp4, EXACTLY 1080x1920, 30 fps, H.264 (yuv420p) + AAC, duration = narration length
  (+0.5 s max tail). Verify with ffprobe before you stop; if it is smaller, re-render at full size.
- Picture follows timing.json: each beat's shot starts at its "start" and shows that beat's "visual" on its
  "backdrop". At least 7 clearly different backdrops/sets across the film, a new set or shot every 3-6 s, never
  hold one background longer than 8 s. Frame 1 is already mid-action (no fade in from black).
- Captions: the narration verbatim, 2-4 word semantic chunks timed to timing.json words, large and readable,
  centred between y=1150 and y=1450 (never below 1500: platform UI covers it), one highlighted keyword per chunk
  (use caption_keyword when it appears). Keep the right 160 px clear of important action (like/comment buttons).
- Sound: narration on top. Music bed and SFX must be synthesized locally or come from the Skill's own bundled
  assets; nothing downloaded. Music bed around -28 dB under the voice; small SFX on scene changes.
- Content: follow {work}/content-policy.md for every drawing (e.g. never depict the Prophet Muhammad; living-faith
  figures drawn only respectfully; no gore).
- Free-only: no paid services, no API keys, no browser automation, no web downloads.
- Human approval gates in the Skill are impossible here: take the most conservative choice and list each bypassed
  gate in {work}/out/NOTES.md. Never claim an approval happened.
- Never open or view image/PNG files in this session (image reads hang this model); check frames with ffprobe /
  ffmpeg numbers only. Keep command output short. Scratch files go in {work}/tmp, never /tmp.
- Ship order: the moment the MP4 renders, copy it to {work}/out/video.mp4, then write {work}/out/NOTES.md
  (style, backdrops list, what was bypassed, any problems), then stop. Do not stop before the MP4 exists.
- If a step fails twice, simplify the scene rather than retrying the same thing.
