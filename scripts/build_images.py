import json
import re
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageOps, ImageEnhance, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "image-sources.json"
OUTPUT_DIR = ROOT / "generated"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = "kindle-weather-builder/1.0"

SHARPNESS_FACTOR = 1.8
CONTRAST_FACTOR = 2.0
THRESHOLD = 195
AUTO_CONTRAST_CUTOFF = 0

KINDLE_WIDTH = 1072
KINDLE_HEIGHT = 1448
HORIZONTAL_MARGIN = 20

COMBINATIONS = [
    ("weather01", "weather", "weather1"),
    ("weather23", "weather2", "weather3"),
    ("weather45", "weather4", "weather5"),
]

FONT_SIZE = 28
TEXT_PADDING_X = 18
TEXT_PADDING_Y = 16
TEXT_LINE_SPACING = 6

ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
]


def load_sources():
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    required = ["weather", "weather1", "weather2", "weather3", "weather4", "weather5"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError(f"Nedostaju URL-ovi za: {', '.join(missing)}")

    return data


def load_font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, FONT_SIZE)
    return ImageFont.load_default()


def strip_ansi(text: str) -> str:
    text = ANSI_ESCAPE_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip("\n")


def render_text_to_image(text: str) -> Image.Image:
    text = strip_ansi(text)

    if not text.strip():
        raise ValueError("Tekstualni odgovor je prazan.")

    font = load_font()
    lines = text.split("\n")

    dummy = Image.new("RGB", (10, 10), (255, 255, 255))
    draw = ImageDraw.Draw(dummy)

    max_width = 0
    line_heights = []

    for line in lines:
        probe = line if line else " "
        bbox = draw.textbbox((0, 0), probe, font=font)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        max_width = max(max_width, width)
        line_heights.append(max(height, FONT_SIZE))

    content_height = sum(line_heights)
    if len(lines) > 1:
        content_height += (len(lines) - 1) * TEXT_LINE_SPACING

    image_width = max_width + (2 * TEXT_PADDING_X)
    image_height = content_height + (2 * TEXT_PADDING_Y)

    img = Image.new("RGB", (image_width, image_height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    y = TEXT_PADDING_Y
    for i, line in enumerate(lines):
        draw.text((TEXT_PADDING_X, y), line, font=font, fill=(0, 0, 0))
        y += line_heights[i] + TEXT_LINE_SPACING

    return img


def fetch_source_as_image(url: str) -> Image.Image:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/plain,image/png,image/*;q=0.8,*/*;q=0.5",
        },
    )

    with urlopen(req, timeout=30) as response:
        raw = response.read()
        content_type = response.headers.get("Content-Type", "").lower()

    if not raw:
        raise ValueError(f"Prazan odgovor za URL: {url}")

    if content_type.startswith("image/"):
        img = Image.open(BytesIO(raw))
        img.load()
        return img

    text = raw.decode("utf-8", errors="replace")
    return render_text_to_image(text)


def process_image(img: Image.Image) -> Image.Image:
    if img.mode == "P":
        img = img.convert("RGBA")

    if img.mode in ("RGBA", "LA"):
        img = img.convert("RGBA")
        background = Image.new("RGBA", img.size, (0, 0, 0, 255))
        img = Image.alpha_composite(background, img).convert("RGB")
    else:
        img = img.convert("RGB")

    img = ImageOps.invert(img)
    img = img.convert("L")
    img = ImageEnhance.Sharpness(img).enhance(SHARPNESS_FACTOR)
    img = ImageEnhance.Contrast(img).enhance(CONTRAST_FACTOR)
    img = ImageOps.autocontrast(img, cutoff=AUTO_CONTRAST_CUTOFF)
    # img = img.point(lambda p: 255 if p > THRESHOLD else 0)

    return img


def combine_vertical(top_img: Image.Image, bottom_img: Image.Image) -> Image.Image:
    """
    Kombinirana slika:
    - fiksna veličina Kindle zaslona
    - gornja slika zauzima oko 2/3 visine
    - donja zauzima ostatak
    - mali lijevi/desni margin
    - bez rezanja, ali uz resize/deformaciju po potrebi
    """
    if top_img.mode != "L":
        top_img = top_img.convert("L")
    if bottom_img.mode != "L":
        bottom_img = bottom_img.convert("L")

    width = KINDLE_WIDTH
    height = KINDLE_HEIGHT

    top_height = (height * 2) // 3
    bottom_height = height - top_height

    inner_width = width - (2 * HORIZONTAL_MARGIN)

    top_resized = top_img.resize((inner_width, top_height), Image.Resampling.BICUBIC)
    bottom_resized = bottom_img.resize((inner_width, bottom_height), Image.Resampling.BICUBIC)

    combined = Image.new("L", (width, height), color=255)
    combined.paste(top_resized, (HORIZONTAL_MARGIN, 0))
    combined.paste(bottom_resized, (HORIZONTAL_MARGIN, top_height))

    return combined


def save_png(img: Image.Image, path: Path) -> None:
    img.save(path, format="PNG", optimize=True)


def main() -> int:
    try:
        sources = load_sources()
    except Exception as e:
        print(f"[ERROR] Konfiguracija: {e}", file=sys.stderr)
        return 1

    status = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "files": {}
    }

    processed_images = {}

    # 1) Skini i obradi pojedinačne slike
    for name, url in sources.items():
        out_path = OUTPUT_DIR / f"{name}.png"
        try:
            print(f"[INFO] Skidam {name} sa {url}")
            img = fetch_source_as_image(url)

            print(f"[INFO] Obradujem {name}")
            inv = process_image(img)

            save_png(inv, out_path)
            processed_images[name] = inv

            status["files"][name] = {
                "url": url,
                "output": str(out_path.relative_to(ROOT)),
                "ok": True
            }
            print(f"[OK] Spremljeno: {out_path}")
        except Exception as e:
            status["files"][name] = {
                "url": url,
                "output": str(out_path.relative_to(ROOT)),
                "ok": False,
                "error": str(e)
            }
            print(f"[ERROR] {name}: {e}", file=sys.stderr)
            return 1

    # 2) Napravi spojene slike
    for combined_name, top_name, bottom_name in COMBINATIONS:
        out_path = OUTPUT_DIR / f"{combined_name}.png"
        try:
            print(f"[INFO] Spajam {top_name} + {bottom_name} -> {combined_name}")
            combined_img = combine_vertical(
                processed_images[top_name],
                processed_images[bottom_name]
            )

            save_png(combined_img, out_path)

            status["files"][combined_name] = {
                "source_images": [top_name, bottom_name],
                "output": str(out_path.relative_to(ROOT)),
                "ok": True
            }
            print(f"[OK] Spremljeno: {out_path}")
        except Exception as e:
            status["files"][combined_name] = {
                "source_images": [top_name, bottom_name],
                "output": str(out_path.relative_to(ROOT)),
                "ok": False,
                "error": str(e)
            }
            print(f"[ERROR] {combined_name}: {e}", file=sys.stderr)
            return 1

    status_path = OUTPUT_DIR / "status.json"
    status_path.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Status spremljen: {status_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
