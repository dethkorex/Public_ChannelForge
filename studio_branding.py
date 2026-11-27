"""Render ChannelForge interstitials from the local Studio schedule."""

from io import BytesIO
import math
import os
from pathlib import Path
import re
import struct
import wave

from PIL import Image, ImageDraw, ImageFont, ImageOps


AMBIENCE_SECONDS = 25
DEFAULT_CARD_SECONDS = 18
DEFAULT_AMBIENCE = "soft-synth"
AMBIENCE_RATE = 24_000
AMBIENCE = {"silent": "Silent", "soft-synth": "Soft synth", "station-chime": "Station chime", "night-signal": "Night signal"}
STYLES = {
    "studio": ("Clean Studio", "#31d8ff", "#9b6cff"),
    "rewind": ("Rewind", "#f2bc72", "#da56a9"),
    "midnight": ("Midnight", "#be9fff", "#526eff"),
}


def card_font(size):
    for font in (Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/segoeuib.ttf",
                 Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
                 Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf")):
        if font.is_file():
            return ImageFont.truetype(str(font), size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def settings(value=None, *, enabled=False):
    value = value if isinstance(value, dict) else {}
    try:
        duration = int(value.get("seconds", DEFAULT_CARD_SECONDS))
    except (TypeError, ValueError, OverflowError):
        duration = DEFAULT_CARD_SECONDS
    style = str(value.get("style", "studio"))
    sound = str(value.get("ambience", "silent"))[:100]
    return {"enabled": value.get("enabled", enabled) is True, "seconds": max(5, min(AMBIENCE_SECONDS, duration)),
            "style": style if style in STYLES else "studio", "ambience": sound}


def programme_title(program):
    return str(program.get("series") or "").strip() or str(program.get("title") or "").strip() or "Next programme"


def with_interstitials(programs, branding, *, looping=False):
    if not programs or (len(programs) == 1 and not looping) or not branding["enabled"]:
        return programs
    slots = []
    for index, program in enumerate(programs):
        slots.append(program)
        # A finite lineup ends with its last programme. The card between two
        # programmes is part of the clock, including when nobody is watching.
        if looping or index + 1 < len(programs):
            next_program = programs[(index + 1) % len(programs)]
            slots.append({"id": f"up-next-{index}", "source": "interstitial", "type": "interstitial",
                          "title": "Up Next · " + programme_title(next_program),
                          "duration_ms": branding["seconds"] * 1000, "next_program": next_program})
    return slots


def open_artwork(data, mode="RGB"):
    if not data:
        return None
    try:
        with Image.open(BytesIO(data)) as image:
            if image.width * image.height > 16_000_000:
                return None
            return ImageOps.exif_transpose(image).convert(mode)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def fit_text(draw, text, font, width):
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def title_lines(draw, title, font, width):
    if draw.textlength(title, font=font) <= width:
        return [title]
    first = ""
    words = title.split()
    while words and draw.textlength((first + " " + words[0]).strip(), font=font) <= width:
        first = (first + " " + words.pop(0)).strip()
    if not first:
        return [fit_text(draw, title, font, width)]
    return [first, fit_text(draw, " ".join(words), font, width)]


def render_card(branding, channel, program, poster=None, logo=None):
    """Use the authorised library's artwork and the upcoming scheduled title."""
    _, accent, secondary = STYLES[branding["style"]]
    canvas = Image.new("RGB", (960, 540), "#090b10")
    draw = ImageDraw.Draw(canvas)
    for y in range(540):
        draw.line((0, y, 959, y), fill=(9 + y // 63, 11 + y // 38, 16 + y // 26))
    draw.rounded_rectangle((34, 42, 925, 498), radius=26, fill="#11141b", outline="#272c38", width=2)
    if logo is not None:
        badge = ImageOps.contain(logo, (64, 60), method=Image.Resampling.LANCZOS)
        canvas.paste(badge, (54 + (64 - badge.width) // 2, 72 + (60 - badge.height) // 2), badge)
    else:
        draw.rounded_rectangle((54, 72, 118, 132), radius=12, fill=accent)
        draw.polygon([(69, 84), (106, 84), (106, 94), (79, 94), (79, 109), (98, 109), (98, 120), (69, 120)], fill="#041116")
        draw.polygon([(93, 98), (111, 103), (93, 109)], fill="#041116")
    heading = card_font(23)
    title_font = card_font(49)
    description = card_font(25)
    station = f"CH {channel.get('number', '—')}  ·  {channel.get('name', 'ChannelForge')}"
    draw.text((138, 86), fit_text(draw, station, heading, 456), fill="#f5f7fb", font=heading)
    draw.text((62, 203), "COMING UP NEXT", fill=accent, font=heading)
    series = str(program.get("series") or "").strip()
    program_title = str(program.get("title") or "").strip()
    title = programme_title(program)
    lines = title_lines(draw, title, title_font, 554)
    for number, line in enumerate(lines):
        draw.text((62, 234 + 51 * number), line, fill="#f5f7fb", font=title_font)
    detail = program_title if series else str(program.get("year") or "")
    if series and detail.casefold().startswith((series + " · ").casefold()):
        detail = detail[len(series) + 3:]
    if (series and detail and detail != title and not re.match(r"^S\d+E\d+\b", detail, re.IGNORECASE)
            and isinstance(program.get("season"), int) and isinstance(program.get("episode"), int)
            and program["episode"] > 0):
        detail = f"S{program['season']:02d}E{program['episode']:02d} · {detail}"
    if detail and detail != title:
        draw.text((64, 294 + 51 * (len(lines) - 1)), fit_text(draw, detail, description, 550), fill="#acb5c7", font=description)
    draw.rounded_rectangle((63, 395, 578, 400), radius=2, fill=secondary)
    if poster is not None:
        size = (243, 365)
        if poster.width > poster.height:
            artwork = ImageOps.pad(poster, size, method=Image.Resampling.LANCZOS, color="#171b24")
        else:
            artwork = ImageOps.fit(poster, size, method=Image.Resampling.LANCZOS)
        mask = Image.new("L", size)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, 242, 364), radius=15, fill=255)
        canvas.paste(artwork, (657, 88), mask)
    else:
        draw.rounded_rectangle((657, 88, 900, 453), radius=15, fill="#171b24", outline=accent, width=2)
        draw.polygon([(747, 228), (747, 315), (830, 272)], fill=accent)
    return canvas.resize((1280, 720), Image.Resampling.LANCZOS)


def default_art(style, kind):
    label, accent, secondary = STYLES[style]
    width, height = (320, 320) if kind == "corner-bug" else (960, 540)
    image = Image.new("RGBA", (width, height), "#090b10" if kind != "corner-bug" else (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if kind == "guide-background":
        for y in range(0, height, 28):
            draw.line((0, y, width, y), fill="#272c38", width=1)
        for x in range(0, width, 70):
            draw.line((width // 2, height // 4, x + 75, height), fill="#272c38", width=2)
        draw.rounded_rectangle((32, 34, 928, 506), radius=30, outline=accent, width=2)
        draw.line((88, 494, 485, 494), fill=accent, width=4)
        draw.line((490, 494, 870, 494), fill=secondary, width=4)
        return image
    side = 230 if kind == "corner-bug" else 192
    x, y = (45, 45) if kind == "corner-bug" else (82, 174)
    draw.rounded_rectangle((x, y, x + side, y + side), radius=side // 5, fill=accent)
    shape = [(0.22, .2), (.77, .2), (.77, .34), (.36, .34), (.36, .62), (.65, .62), (.65, .79), (.22, .79)]
    draw.polygon([(x + a * side, y + b * side) for a, b in shape], fill="#041116")
    draw.polygon([(x + .55 * side, y + .39 * side), (x + .84 * side, y + .5 * side),
                  (x + .55 * side, y + .6 * side)], fill="#041116")
    if kind != "corner-bug":
        draw.text((313, 207), "ChannelForge", fill="#f5f7fb", font=card_font(69))
        draw.text((319, 306), label.upper(), fill=accent, font=card_font(23))
        draw.rounded_rectangle((319, 351, 774, 357), radius=3, fill=secondary)
    return image


def write_ambience(target: Path, preset: str):
    """Write an original, voice-free 25-second bed with a gentle fade and low peak."""
    tones = {"soft-synth": (110., 164.81, 220.), "station-chime": (220., 277.18, 329.63),
             "night-signal": (65.41, 98., 130.81)}.get(preset, ())
    count = AMBIENCE_RATE * AMBIENCE_SECONDS
    samples = bytearray() if tones else bytes(count * 2)
    for index in range(count if tones else 0):
        t = index / AMBIENCE_RATE
        fade = min(1.0, t / 1.6, (AMBIENCE_SECONDS - t) / 2.5)
        level = .65 + .35 * math.sin(math.tau * (.2 if preset == "station-chime" else .09) * t)
        wave_sum = sum(math.sin(math.tau * pitch * t) for pitch in tones)
        samples.extend(struct.pack("<h", int(970 * fade * level * wave_sum)))
    with wave.open(str(target), "wb") as audio:
        audio.setparams((1, 2, AMBIENCE_RATE, 0, "NONE", "not compressed"))
        audio.writeframes(samples)
