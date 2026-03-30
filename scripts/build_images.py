import json
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "image-sources.json"
OUTPUT_DIR = ROOT / "generated"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = "kindle-weather-builder/1.0"


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


def invert_image(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA"):
        rgba = img.convert("RGBA")
        r, g, b, a = rgba.split()
        rgb = Image.merge("RGB", (r, g, b))
        inverted_rgb = ImageOps.invert(rgb)
        r2, g2, b2 = inverted_rgb.split()
        return Image.merge("RGBA", (r2, g2, b2, a))

    if img.mode == "P":
        img = img.convert("RGBA")
        return invert_image(img)

    if img.mode != "RGB":
        img = img.convert("RGB")

    return ImageOps.invert(img)


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

    for name, url in sources.items():
        out_path = OUTPUT_DIR / f"{name}.png"
        try:
            print(f"[INFO] Skidam {name} sa {url}")
            img = download_image(url)
            print(f"[INFO] Invertiram {name}")
            inv = invert_image(img)
            save_png(inv, out_path)
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

    status_path = OUTPUT_DIR / "status.json"
    status_path.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Status spremljen: {status_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())