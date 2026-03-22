"""NanumGothic 폰트를 GitHub에서 다운로드하여 assets/fonts/에 저장"""
import urllib.request
from pathlib import Path

FONTS = {
    "NanumGothic.ttf": (
        "https://github.com/google/fonts/raw/main/"
        "ofl/nanumgothic/NanumGothic-Regular.ttf"
    ),
    "NanumGothicBold.ttf": (
        "https://github.com/google/fonts/raw/main/"
        "ofl/nanumgothic/NanumGothic-Bold.ttf"
    ),
}

font_dir = Path(__file__).parent.parent / "assets" / "fonts"
font_dir.mkdir(parents=True, exist_ok=True)

for filename, url in FONTS.items():
    dest = font_dir / filename
    if dest.exists():
        print(f"{filename} already exists, skipping.")
        continue
    print(f"Downloading {filename}...")
    urllib.request.urlretrieve(url, dest)
    print(f"Saved: {dest}")
