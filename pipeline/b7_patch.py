"""B7 caption helper patch (carried over from the original bot, where it fixed missing word
spaces in pixel captions). Appends draw_word_caption to a project-local copy of the Skill's
b7_pixel_primitives.py and proves full-line geometry matches before any render."""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

PATCH = '''


def draw_word_caption(image, value, highlight_index=0, y=264, scale=1,
                      color=CREAM, highlight=TEAL):
    """Draw a complete line; spaces retain the exact bitmap-font advance."""
    import re
    value = _text(value, GLYPHS)
    if value != " ".join(value.split()) or not value:
        raise ValueError("Use nonempty words separated by one literal space")
    scale = _scale(scale)
    width, height = text_size(value, scale)
    box_width = width + scale
    if box_width > image.width * .65:
        raise ValueError("Choose a shorter semantic cue; never delete spaces or shrink type")
    if y < 0 or y + height + scale > image.height:
        raise ValueError("Caption outside frame")
    x = (image.width - box_width) // 2
    if min(x, image.width - x - box_width) < image.width * .175:
        raise ValueError("Caption margin too small")
    draw = ImageDraw.Draw(image)
    word = -1
    for token in re.findall(r" +|[^ ]+", value):
        if not token.isspace():
            word += 1
            draw_label(draw, (x, y), token,
                       highlight if word == highlight_index else color, scale, True)
        # text_size removes the final letter gap; restore it for every token.
        # In particular a space advances 3 blank columns + 1 letter gap.
        x += text_size(token, scale)[0] + scale
    left = (image.width - box_width) // 2
    return left, y, box_width, height + scale
'''


def install(skill_dir: Path, project: Path) -> Path:
    src = Path(skill_dir) / "scripts" / "b7_pixel_primitives.py"
    dst = Path(project) / "b7_pixel_primitives.py"
    shutil.copy(src, dst)
    dst.write_text(dst.read_text(encoding="utf-8") + PATCH, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("b7pixel", dst)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    from PIL import Image
    assert m.text_size("YOU HATE")[0] > m.text_size("YOUHATE")[0], "word spaces lost"
    bg = (21, 32, 54)
    for line, y in [("YOU HATE", 60), ("PAST COSTS ARE GONE", 140)]:
        a = Image.new("RGB", (180, 320), "#152036")
        b = a.copy()
        m.draw_word_caption(a, line, 0, y)
        m.draw_caption(b, line, y=y, margin=32)
        assert [px != bg for px in a.getdata()] == [px != bg for px in b.getdata()], "caption geometry mismatch"
    return dst
