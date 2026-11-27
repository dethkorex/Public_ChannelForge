"""Export platform sizes from the desktop icons, Fire TV banner and Roku FHD tile."""

from pathlib import Path

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
FIRE_RES = ROOT / "tv" / "firetv" / "app" / "src" / "main" / "res"
ROKU_IMAGES = ROOT / "tv" / "roku" / "images"


def contained(source: Image.Image, size: tuple[int, int]) -> Image.Image:
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    image = source.convert("RGBA")
    image.thumbnail(size, Image.Resampling.LANCZOS)
    canvas.alpha_composite(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return canvas


def main() -> None:
    creator = Image.open(ASSETS / "channel-forge-creator-icon.png")
    television = Image.open(ASSETS / "channel-forge-tv-icon.png")
    windows_sizes = [(size, size) for size in (16, 24, 32, 48, 64, 128, 256)]
    for name, icon in (("creator", creator), ("tv", television)):
        icon.save(ASSETS / f"channel-forge-{name}.ico", sizes=windows_sizes)
    contained(creator, (96, 96)).save(ASSETS / "channel-forge-creator-installer.png", optimize=True)

    for density, pixels in (("mdpi", 48), ("hdpi", 72), ("xhdpi", 96), ("xxhdpi", 144)):
        launcher = FIRE_RES / f"mipmap-{density}" / "channel_forge_tv_icon.png"
        contained(television, (pixels, pixels)).save(launcher, optimize=True)
    contained(television, (512, 512)).save(FIRE_RES / "drawable" / "channel_forge_tv_icon.png", optimize=True)
    contained(television, (256, 256)).save(FIRE_RES / "drawable-nodpi" / "channel_forge_viewer_brand.png", optimize=True)

    banner = Image.open(FIRE_RES / "drawable-nodpi" / "channel_forge_tv_banner.png").convert("RGB")
    roku = Image.open(ROKU_IMAGES / "channel-icon-fhd.png").convert("RGB")
    for label, size in (("hd", (290, 218)), ("sd", (214, 144))):
        tile = ImageOps.fit(roku, size, Image.Resampling.LANCZOS, centering=(0.5, 0.0))
        tile.save(ROKU_IMAGES / f"channel-icon-{label}.png", optimize=True)

    for label, size in (("fhd", (1920, 1080)), ("hd", (1280, 720)), ("sd", (720, 480))):
        splash = ImageOps.fit(banner, size, Image.Resampling.LANCZOS)
        splash.save(ROKU_IMAGES / f"splash-{label}.jpg", quality=89, optimize=True)


if __name__ == "__main__":
    main()
