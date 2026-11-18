"""Downloads The Game Awards' own nominee pictures, shrinks them, and links them in the year's ballot.

The list (scripts/tga_<year>.txt: "name as TGA writes it|file") is read off thegameawards.com/categories/* by hand:
that site sits behind a Cloudflare check, but its pictures (assets.thegameawards.com) do not. They are 1080x1080
squares of 300-800 KB each; here they become SIZE x SIZE JPEGs in static/img/<year>/, served by this app itself, and
the ballot's nominees get them as their third field ("img/2026/gta6.jpg"). Nominees not in the list keep their picture
from images.json (scripts/fetch_images.py).

Needs Pillow, which the app itself does not:  python -m pip install pillow
    python scripts/fetch_tga_images.py 2026
"""

import io
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ASSETS = "https://assets.thegameawards.com/{year}/11/"
SIZE = 480
HEADERS = {"User-Agent": "imbul-awards-image-fetch/1.0 (one-off script)"}

# Our name -> TGA's, where they differ beyond quotes and case
ALIASES = {
    "fallout (2. évad)": "fallout (season 2)",
    "ttk testing": "ttk",
}


def key(name):
    return name.replace("’", "'").casefold().strip()


def read_list(year):
    entries = {}
    for line in (ROOT / "scripts" / f"tga_{year}.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, file = line.rsplit("|", 1)
        entries[key(name)] = file.strip()
    return entries


def local_name(file):
    """'5671c9cc-gta6.jpg' -> 'gta6.jpg': TGA's hash prefix off, always a JPEG."""
    stem = re.sub(r"^[0-9a-f]{8}-", "", Path(file).stem).lower()
    return re.sub(r"[^a-z0-9_-]+", "-", stem).strip("-") + ".jpg"


def download(year, file, target):
    if target.exists():
        return
    request = urllib.request.Request(ASSETS.format(year=year) + file, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        picture = Image.open(io.BytesIO(response.read()))
    if picture.mode in ("RGBA", "LA", "P"):
        # Transparent PNGs on the page's card colour, not on black
        picture = picture.convert("RGBA")
        background = Image.new("RGBA", picture.size, (15, 20, 36, 255))
        picture = Image.alpha_composite(background, picture)
    picture = picture.convert("RGB")
    side = min(picture.size)  # a square from the middle, in case one is not square
    left, top = (picture.width - side) // 2, (picture.height - side) // 2
    picture = picture.crop((left, top, left + side, top + side)).resize((SIZE, SIZE), Image.LANCZOS)
    target.parent.mkdir(parents=True, exist_ok=True)
    picture.save(target, "JPEG", quality=82, optimize=True, progressive=True)
    time.sleep(0.3)


def dump_ballot(ballot):
    """The ballot as written by hand: one category per block, one nominee per line."""
    def one(value):
        return json.dumps(value, ensure_ascii=False)

    head = [f" {one(k)}: {one(v)}," for k, v in ballot.items() if k not in ("sources", "categories")]
    sources = ",\n".join(f"  {one(source)}" for source in ballot.get("sources", []))
    categories = []
    for category in ballot["categories"]:
        fields = ", ".join(f"{one(k)}: {one(v)}" for k, v in category.items() if k != "nominees")
        nominees = ",\n".join(f"   {one(nominee)}" for nominee in category["nominees"])
        categories.append(f"  {{{fields}, \"nominees\": [\n{nominees}]}}")
    return ("{\n" + "\n".join(head) + f"\n \"sources\": [\n{sources}\n ],\n \"categories\": [\n"
            + ",\n".join(categories) + "\n ]\n}\n")


def main():
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    entries = read_list(year)
    ballot_path = ROOT / "ballots" / f"{year}.json"
    ballot = json.loads(ballot_path.read_text(encoding="utf-8"))
    missing = []
    for category in ballot["categories"]:
        for nominee in category["nominees"]:
            name, detail = nominee[0], nominee[1]
            wanted = ALIASES.get(key(name), key(name))
            file = entries.get(f"{wanted} ({key(detail)})") or entries.get(wanted)  # Team Falcons (Rocket League)
            if file is None:
                missing.append(f"{category['id']}: {name}")
                continue
            target = ROOT / "static" / "img" / str(year) / local_name(file)
            download(year, file, target)
            path = f"img/{year}/{target.name}"
            if len(nominee) > 2:
                nominee[2] = path
            else:
                nominee.append(path)
    ballot_path.write_text(dump_ballot(ballot), encoding="utf-8", newline="\n")
    print(f"{year}: {sum(len(c['nominees']) for c in ballot['categories']) - len(missing)} nominees linked,"
          f" missing: {missing or 'none'}")


if __name__ == "__main__":
    main()
