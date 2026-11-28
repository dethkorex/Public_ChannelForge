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
AMBIENCE.update({"christmas-bells": "Christmas bell jingle", "halloween-night": "Halloween night",
                 "arcade-steps": "Arcade steps", "drive-in-overture": "Drive-in overture",
                 "space-drift": "Space drift", "woodland-chimes": "Woodland chimes"})
STYLES = {
    "studio": ("Clean Studio", "#31d8ff", "#9b6cff"),
    "rewind": ("Rewind", "#f2bc72", "#da56a9"),
    "midnight": ("Midnight", "#be9fff", "#526eff"),
    "christmas": ("Christmas Broadcast", "#f8d992", "#3aa481"),
    "halloween": ("Halloween After Dark", "#ffb063", "#a56ad8"),
    "arcade": ("Arcade Glow", "#55ffdc", "#fa55c7"),
    "drive-in": ("Drive-In Premiere", "#ffe0a1", "#ff676f"),
    "cartoons": ("Saturday Cartoons", "#ffea79", "#6ee5f4"),
    "space": ("Space Signal", "#a1eaff", "#8e82ff"),
    "noir": ("Noir Matinee", "#f2e2bd", "#aaa99f"),
    "nature": ("Nature Watch", "#d0ed92", "#74c3a1"),
    "match-night": ("Match Night", "#c7fc4e", "#56a9ff"),
    "newsroom": ("Newsroom", "#c2ecff", "#ee4859"),
}
THEME_SCENES = {
    "rewind": ("#241521", "#512c42", "left", "drive-in-overture"),
    "midnight": ("#171437", "#302861", "right", "night-signal"),
    "christmas": ("#0f2926", "#184138", "left", "christmas-bells"),
    "halloween": ("#21142e", "#44253c", "right", "halloween-night"),
    "arcade": ("#170e38", "#47235e", "left", "arcade-steps"),
    "drive-in": ("#211a2b", "#61364a", "right", "drive-in-overture"),
    "cartoons": ("#143956", "#295c7a", "left", "arcade-steps"),
    "space": ("#101b3f", "#243768", "right", "space-drift"),
    "noir": ("#24252a", "#494645", "left", "night-signal"),
    "nature": ("#1e392f", "#355e46", "right", "woodland-chimes"),
    "match-night": ("#13282e", "#2a5151", "left", "station-chime"),
    "newsroom": ("#17243d", "#313f66", "right", "station-chime"),
}


def style_options():
    return [{"id": key, "label": label, "ambience": THEME_SCENES.get(key, (None, None, None, DEFAULT_AMBIENCE))[3]}
            for key, (label, _, _) in STYLES.items()]


def paint_theme(image, style):
    """Draw artwork on a 960 × 540 canvas while leaving the card text readable."""
    background, pattern, _, _ = THEME_SCENES[style]
    _, accent, detail = STYLES[style]
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 959, 539), fill=background)
    if style == "christmas":
        for x in range(31, 955, 89):
            y = 96 + x * 13 % 132
            draw.polygon(((x, y), (x - 38, 470), (x + 38, 470)), fill=pattern)
            draw.line((x - 7, 503, x + 7, 503), fill=accent, width=3)
        for index in range(88):
            x, y = (53 * index + 15) % 960, (79 * index * index + 11) % 540
            radius = 1 + index % 3
            draw.ellipse((x, y, x + radius * 2, y + radius * 2), fill=accent if index % 7 == 0 else "#9cbab4")
        draw.arc((28, -87, 928, 78), 10, 170, fill=detail, width=5)
    elif style == "halloween":
        draw.ellipse((667, 20, 919, 272), fill=pattern, outline=accent, width=3)
        draw.polygon(((0, 463), (147, 379), (282, 453), (517, 346), (749, 458), (959, 368), (959, 539), (0, 539)), fill="#100a1c")
        for x, y in ((49, 80), (236, 32), (895, 303), (702, 100)):
            draw.polygon(((x, y + 8), (x + 12, y), (x + 28, y + 13), (x + 43, y), (x + 57, y + 8), (x + 29, y + 27)), fill=detail)
        draw.arc((710, 422, 835, 534), 195, 345, fill=accent, width=4)
    elif style == "arcade":
        for x in range(-840, 1780, 150):
            draw.line((480, 270, x, 540), fill=pattern, width=3)
        for y in (301, 322, 359, 413, 487, 523):
            draw.line((0, y, 960, y), fill=pattern, width=3)
        for index in range(39):
            x, y = (index * 83) % 950, (index * 59) % 275
            draw.rectangle((x, y, x + 5, y + 5), fill=accent if index % 4 == 0 else detail)
    elif style in {"drive-in", "rewind"}:
        draw.rectangle((20, 13, 939, 526), outline=pattern, width=15)
        for x in range(28, 939, 54):
            if style == "drive-in":
                draw.ellipse((x, 18, x + 11, 29), fill=accent)
                draw.ellipse((x, 510, x + 11, 521), fill=accent)
            else:
                draw.rounded_rectangle((x, 18, x + 16, 29), radius=2, fill=accent)
                draw.rounded_rectangle((x, 510, x + 16, 521), radius=2, fill=accent)
        draw.polygon(((31, 490), (480, 110), (925, 490)), outline=detail, width=4)
    elif style == "cartoons":
        points = [(480 + math.cos(index * math.pi / 14) * (650 if index % 2 == 0 else 320),
                   270 + math.sin(index * math.pi / 14) * (520 if index % 2 == 0 else 260)) for index in range(28)]
        draw.polygon(points, fill=pattern, outline=accent, width=5)
        for x in range(18, 962, 29):
            for y in (22, 46, 505, 527):
                draw.ellipse((x, y, x + 7, y + 7), fill=detail)
    elif style in {"space", "midnight"}:
        center = 238 if style == "space" else 722
        for radius in (115, 150, 190, 234):
            draw.ellipse((center - radius, 270 - radius // 2, center + radius, 270 + radius // 2), outline=pattern, width=3)
        draw.ellipse((center - 89, 182, center + 89, 360), fill=pattern, outline=detail, width=4)
        draw.arc((center - 195, 189, center + 195, 354), 10, 173, fill=accent, width=4)
        for index in range(63):
            x, y = (index * 167 + 21) % 955, (index * 61 + 8) % 532
            draw.ellipse((x, y, x + 2 + index % 2, y + 2 + index % 2), fill=accent)
    elif style == "noir":
        draw.polygon(((854, 0), (947, 0), (525, 540), (137, 540)), fill=pattern)
        for offset in (12, 21, 31):
            draw.polygon(((offset, 129), (129, offset), (831, offset), (960 - offset, 129),
                          (960 - offset, 411), (831, 540 - offset), (129, 540 - offset), (offset, 411)), outline=accent, width=2)
    elif style == "nature":
        draw.ellipse((92, 28, 232, 168), fill=pattern, outline=accent, width=3)
        for ridge, shade in ((67, pattern), (127, "#233e35"), (201, "#142d28")):
            points = [(x, 190 + ridge + int(53 * math.sin((x + ridge) / 83))) for x in range(0, 981, 28)]
            draw.polygon([*points, (960, 540), (0, 540)], fill=shade, outline=detail, width=2)
    elif style == "match-night":
        for x in range(-480, 980, 103):
            draw.polygon(((x, 0), (x + 51, 0), (x + 529, 540), (x + 478, 540)), fill=pattern)
        draw.rectangle((24, 25, 936, 514), outline=accent, width=3)
        draw.ellipse((390, 180, 570, 360), outline=detail, width=3)
        draw.line((480, 25, 480, 514), fill=detail, width=2)
    elif style == "newsroom":
        draw.polygon(((0, 310), (620, 24), (960, 24), (960, 225), (339, 507), (0, 507)), fill=pattern)
        for y, length, colour in ((33, 585, detail), (47, 712, accent), (496, 909, detail), (510, 375, accent)):
            draw.rounded_rectangle((23, y, length, y + 5), radius=2, fill=colour)


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
    style = branding["style"]
    _, accent, secondary = STYLES[style]
    canvas = Image.new("RGB", (960, 540), "#090b10")
    draw = ImageDraw.Draw(canvas)
    text_x, poster_x = 62, 657
    if style in THEME_SCENES:
        paint_theme(canvas, style)
        draw = ImageDraw.Draw(canvas, "RGBA")
        if THEME_SCENES[style][2] == "right":
            text_x, poster_x = 360, 61
        draw.rounded_rectangle((text_x - 22, 51, text_x + 569, 487), radius=24, fill=(7, 10, 22, 219), outline=secondary, width=2)
        draw.rounded_rectangle((poster_x - 5, 83, poster_x + 247, 457), radius=19, fill="#171b24", outline=accent, width=3)
    else:
        for y in range(540):
            draw.line((0, y, 959, y), fill=(9 + y // 63, 11 + y // 38, 16 + y // 26))
        draw.rounded_rectangle((34, 42, 925, 498), radius=26, fill="#11141b", outline="#272c38", width=2)
    badge_x = text_x - 8
    if logo is not None:
        badge = ImageOps.contain(logo, (64, 60), method=Image.Resampling.LANCZOS)
        canvas.paste(badge, (badge_x + (64 - badge.width) // 2, 72 + (60 - badge.height) // 2), badge)
    else:
        draw.rounded_rectangle((badge_x, 72, badge_x + 64, 132), radius=12, fill=accent)
        emblem = [(15, 84), (52, 84), (52, 94), (25, 94), (25, 109), (44, 109), (44, 120), (15, 120)]
        draw.polygon([(badge_x + x, y) for x, y in emblem], fill="#041116")
        draw.polygon([(badge_x + 39, 98), (badge_x + 57, 103), (badge_x + 39, 109)], fill="#041116")
    heading = card_font(23)
    title_font = card_font(49)
    description = card_font(25)
    station = f"CH {channel.get('number', '—')}  ·  {channel.get('name', 'ChannelForge')}"
    draw.text((text_x + 76, 86), fit_text(draw, station, heading, 456), fill="#f5f7fb", font=heading)
    draw.text((text_x, 203), "COMING UP NEXT", fill=accent, font=heading)
    series = str(program.get("series") or "").strip()
    program_title = str(program.get("title") or "").strip()
    title = programme_title(program)
    lines = title_lines(draw, title, title_font, 554)
    for number, line in enumerate(lines):
        draw.text((text_x, 234 + 51 * number), line, fill="#f5f7fb", font=title_font)
    detail = program_title if series else str(program.get("year") or "")
    if series and detail.casefold().startswith((series + " · ").casefold()):
        detail = detail[len(series) + 3:]
    if (series and detail and detail != title and not re.match(r"^S\d+E\d+\b", detail, re.IGNORECASE)
            and isinstance(program.get("season"), int) and isinstance(program.get("episode"), int)
            and program["episode"] > 0):
        detail = f"S{program['season']:02d}E{program['episode']:02d} · {detail}"
    if detail and detail != title:
        draw.text((text_x + 2, 294 + 51 * (len(lines) - 1)), fit_text(draw, detail, description, 550), fill="#d3dbe8", font=description)
    draw.rounded_rectangle((text_x + 1, 395, text_x + 516, 400), radius=2, fill=secondary)
    if poster is not None:
        size = (243, 365)
        if poster.width > poster.height:
            artwork = ImageOps.pad(poster, size, method=Image.Resampling.LANCZOS, color="#171b24")
        else:
            artwork = ImageOps.fit(poster, size, method=Image.Resampling.LANCZOS)
        mask = Image.new("L", size)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, 242, 364), radius=15, fill=255)
        canvas.paste(artwork, (poster_x, 88), mask)
    else:
        draw.rounded_rectangle((poster_x, 88, poster_x + 243, 453), radius=15, fill="#171b24", outline=accent, width=2)
        draw.polygon([(poster_x + 90, 228), (poster_x + 90, 315), (poster_x + 173, 272)], fill=accent)
    return canvas.resize((1280, 720), Image.Resampling.LANCZOS)


def default_art(style, kind):
    label, accent, secondary = STYLES[style]
    width, height = (320, 320) if kind == "corner-bug" else (960, 540)
    image = Image.new("RGBA", (width, height), "#090b10" if kind != "corner-bug" else (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if style in THEME_SCENES and kind != "corner-bug":
        paint_theme(image, style)
        if kind == "guide-background":
            return image
        draw.rounded_rectangle((39, 124, 921, 416), radius=27, fill="#0b1220", outline=secondary, width=4)
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
    if style in {"halloween", "cartoons", "space", "nature"}:
        draw.ellipse((x, y, x + side, y + side), fill=accent, outline=secondary, width=5)
    elif style in {"arcade", "noir", "match-night"}:
        cut = side // 6
        draw.polygon(((x + cut, y), (x + side, y), (x + side, y + side - cut),
                      (x + side - cut, y + side), (x, y + side), (x, y + cut)), fill=accent, outline=secondary, width=5)
    else:
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
    arrangements = {
        # Beat length, melody (MIDI notes, zero for a rest), drone, timbre and decay.
        "christmas-bells": (.36, (72, 76, 79, 0, 77, 74, 71, 0, 72, 79, 84, 81, 79, 76, 72, 0), 130.81, 3.72, 4.6),
        "halloween-night": (.64, (45, 0, 51, 0, 0, 57, 0, 54, 0, 48, 0, 0, 52, 0, 46, 0), 55.0, 2.02, 2.1),
        "arcade-steps": (.22, (57, 69, 64, 0, 67, 72, 64, 67, 55, 67, 62, 0, 65, 69, 62, 65), 0.0, 3.0, 9.5),
        "drive-in-overture": (.45, (55, 62, 67, 0, 57, 64, 69, 0, 59, 66, 71, 0, 60, 67, 72, 0), 98.0, 2.0, 2.4),
        "space-drift": (.93, (52, 0, 59, 64, 0, 66, 0, 57, 0, 62, 69, 0, 64, 0, 59, 0), 82.41, 2.5, 1.4),
        "woodland-chimes": (.48, (67, 72, 76, 0, 74, 69, 0, 72, 79, 76, 0, 74, 69, 67, 0, 0), 0.0, 2.65, 3.4),
    }
    tones = {"soft-synth": (110., 164.81, 220.), "station-chime": (220., 277.18, 329.63),
             "night-signal": (65.41, 98., 130.81)}.get(preset, ())
    count = AMBIENCE_RATE * AMBIENCE_SECONDS
    if preset in arrangements:
        samples = musical_ambience(count, *arrangements[preset])
    else:
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


def musical_ambience(count, beat, notes, drone, overtone, decay):
    samples = bytearray()
    pitches = [440 * 2 ** ((note - 69) / 12) if note else 0 for note in notes]
    frames_per_note = round(AMBIENCE_RATE * beat)
    for index in range(count):
        step, offset = divmod(index, frames_per_note)
        local_time = offset / AMBIENCE_RATE
        time = index / AMBIENCE_RATE
        pitch = pitches[step % len(pitches)]
        ringing = 0.0
        if pitch:
            phase = math.tau * pitch * local_time
            envelope = min(1.0, local_time / .032, (beat - local_time) / .055)
            ringing = 2400 * envelope * (math.sin(phase) * math.exp(-decay * local_time)
                                           + .28 * math.sin(phase * overtone) * math.exp(-8 * local_time))
        bed = 360 * math.sin(math.tau * drone * time) * (.72 + .28 * math.sin(math.tau * .16 * time)) if drone else 0
        fade = min(1.0, time / .26, (AMBIENCE_SECONDS - time) / 1.2)
        samples.extend(struct.pack("<h", int((ringing + bed) * fade)))
    return samples
