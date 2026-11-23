"""Create deterministic platform icon variants from the two master PNGs."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
FIRE_RES = ROOT / "tv" / "firetv" / "app" / "src" / "main" / "res"
ROKU_IMAGES = ROOT / "tv" / "roku" / "images"


def contained(source: Image.Image, size: tuple[int, int], padding: int = 0) -> Image.Image:
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    image = source.convert("RGBA")
    image.thumbnail((size[0] - padding * 2, size[1] - padding * 2), Image.Resampling.LANCZOS)
    canvas.alpha_composite(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return canvas


def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/seguisb.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def main() -> None:
    creator = Image.open(ASSETS / "channel-forge-creator.png")
    television = Image.open(ASSETS / "channel-forge-tv.png")

    contained(creator, (512, 512), 12).save(
        ASSETS / "channel-forge-creator.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )
    contained(television, (512, 512), 12).save(
        ASSETS / "channel-forge-tv.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )
    contained(creator, (96, 96), 3).save(ASSETS / "channel-forge-creator-installer.png", optimize=True)

    for folder, pixels in (("mipmap-mdpi", 48), ("mipmap-hdpi", 72), ("mipmap-xhdpi", 96), ("mipmap-xxhdpi", 144)):
        contained(television, (pixels, pixels), max(2, pixels // 32)).save(FIRE_RES / folder / "channel_forge_tv_icon.png", optimize=True)
    contained(television, (512, 512), 12).save(FIRE_RES / "drawable" / "channel_forge_tv_icon.png", optimize=True)

    width, height = 1280, 720
    banner = Image.new("RGB", (width, height), "#070817")
    draw = ImageDraw.Draw(banner)
    for y in range(height):
        blend = y / max(1, height - 1)
        draw.line((0, y, width, y), fill=(7 + int(16 * blend), 8, 23 + int(35 * blend)))
    draw.ellipse((760, -250, 1420, 410), fill="#251050")
    draw.ellipse((920, 290, 1390, 760), fill="#3a0b4f")
    icon = contained(television, (550, 550), 18)
    banner.paste(icon, (50, 80), icon)
    draw.text((590, 235), "CHANNELFORGE", font=font(67, True), fill="#fff12e", stroke_width=1, stroke_fill="#fff12e")
    draw.text((594, 326), "TV", font=font(88, True), fill="#5befff")
    draw.text((600, 438), "THE COMMUNITY DIAL", font=font(27, True), fill="#ff48c8")
    banner.save(FIRE_RES / "drawable-nodpi" / "channel_forge_tv_banner.png", optimize=True)

    for label, size in (("fhd", (540, 405)), ("hd", (290, 218)), ("sd", (214, 144))):
        tile = Image.new("RGBA", size, "#070817")
        tile.alpha_composite(contained(television, size, size[1] // 16))
        tile.convert("RGB").save(ROKU_IMAGES / f"channel-icon-{label}.png", optimize=True)
    for label, size in (("fhd", (1920, 1080)), ("hd", (1280, 720)), ("sd", (720, 480))):
        splash = Image.new("RGB", size, "#070817")
        layout = banner.copy()
        layout.thumbnail(size, Image.Resampling.LANCZOS)
        splash.paste(layout, ((size[0] - layout.width) // 2, (size[1] - layout.height) // 2))
        splash.save(ROKU_IMAGES / f"splash-{label}.jpg", quality=89, optimize=True)


if __name__ == "__main__":
    main()
