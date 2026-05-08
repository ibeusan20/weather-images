import json
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageOps, ImageEnhance

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "image-sources.json"
OUTPUT_DIR = ROOT / "generated"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = "kindle-weather-builder/1.0"

SHARPNESS_FACTOR = 1.8
CONTRAST_FACTOR = 2.0
THRESHOLD = 195
AUTO_CONTRAST_CUTOFF = 0

COMBINATIONS = [
    ("weather01", "weather", "weather1"),
    ("weather23", "weather2", "weather3"),
    ("weather45", "weather4", "weather5"),
]


def load_sources():
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    required = ["weather", "weather1", "weather2", "weather3", "weather4", "weather5"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError(f"Nedostaju URL-ovi za: {', '.join(missing)}")

    return data


def download_image(url: str) -> Image.Image:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as response:
        raw = response.read()

    if not raw:
        raise ValueError(f"Prazan odgovor za URL: {url}")

    img = Image.open(BytesIO(raw))
    img.load()
    return img


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


def resize_to_fit(img: Image.Image, max_width: int, max_height: int) -> Image.Image:
    """
    Smanji/povećaj sliku tako da stane unutar zadanog prostora,
    uz očuvanje omjera stranica.
    """
    img = img.copy()
    img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
    return img


def combine_vertical(top_img: Image.Image, bottom_img: Image.Image) -> Image.Image:
    """
    Spaja dvije slike vertikalno tako da:
    - gornja slika zauzima gornje 2/3 ukupne visine
    - donja slika zauzima donju 1/3 ukupne visine

    Slike se skaliraju da stanu u svoju zonu i centriraju se.
    Pozadina je bijela.
    """
    if top_img.mode != "L":
        top_img = top_img.convert("L")
    if bottom_img.mode != "L":
        bottom_img = bottom_img.convert("L")

    # Ukupna širina = veća od dvije širine
    width = max(top_img.width, bottom_img.width)

    # Ukupna visina može ostati zbroj originalnih visina
    total_height = top_img.height + bottom_img.height

    # Zone: top = 2/3, bottom = 1/3
    top_area_height = (total_height * 2) // 3
    bottom_area_height = total_height - top_area_height

    # Resize svake slike da stane u svoju zonu
    top_resized = resize_to_fit(top_img, width, top_area_height)
    bottom_resized = resize_to_fit(bottom_img, width, bottom_area_height)

    # Napravi bijeli canvas
    combined = Image.new("L", (width, total_height), color=255)

    # Centriraj gornju sliku u gornjoj zoni
    top_x = (width - top_resized.width) // 2
    top_y = (top_area_height - top_resized.height) // 2

    # Centriraj donju sliku u donjoj zoni
    bottom_x = (width - bottom_resized.width) // 2
    bottom_y = top_area_height + ((bottom_area_height - bottom_resized.height) // 2)

    combined.paste(top_resized, (top_x, top_y))
    combined.paste(bottom_resized, (bottom_x, bottom_y))

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
            img = download_image(url)

            print(f"[INFO] Invertiram {name}")
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
