"""Finds a picture for every nominee on English Wikipedia and writes images.json ({nominee name: {title, url}}).

The page shows these pictures straight from upload.wikimedia.org (they are not copied into the repo). Run it again
after changing nominees.json; names already in images.json are kept, so checked or hand-fixed entries stay. Names
in OVERRIDES use that Wikipedia article instead of a search; None means no picture (the page draws a placeholder).

    python scripts/fetch_images.py            # only the missing ones
    python scripts/fetch_images.py --all      # search everything again (overrides still win)
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOMINEES = ROOT / "nominees.json"
IMAGES = ROOT / "images.json"
API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "imbul-awards-image-fetch/1.0 (one-off script)"}

# What to add to the search, per category: most nominees are games
SEARCH_HINT = {
    "esports-athlete": "esports player",
    "esports-team": "esports organization",
    "creator": "streamer YouTuber",
    "adaptation": "",
    "esports-game": "video game",
}

# Nominee name -> Wikipedia article (checked by hand), or None for no picture
OVERRIDES = {
    # Best Performance: the cover of the role's game
    "Angela Sant'Albano": "Resident Evil Requiem",
    "Liam McIntyre": "Marvel's Wolverine",
    "Manni L. Perez": "Grand Theft Auto VI",
    "Patrick Gibson": "007 First Light",
    "Rahul Kohli": "Saros (video game)",
    # The search found the wrong article
    "A Dream of Silence: Revamped": "Baldur's Gate 3",
    "Counter-Strike 2": "Counter-Strike 2",
    "Fallout (2. évad)": "Fallout (American TV series)",
    "Fortnite": "Fortnite Battle Royale",
    "Into the Radius 2": "Into the Radius 2",
    "League of Legends": "League of Legends",
    "lily's world XD": "Lily's World XD",
    "Mobile Legends: Bang Bang": "Mobile Legends: Bang Bang",
    "My Hero Academia: All's Justice": "My Hero Academia: All's Justice",
    "Nopixel V": "Grand Theft Auto Online",
    "Resident Evil": "Resident Evil (2026 film)",
    "Slay the Spire 2": "Slay the Spire 2",
    "Star Wars Droid Tycoon": "Fortnite Battle Royale",
    "Teenage Mutant Ninja Turtles: Empire City": "Teenage Mutant Ninja Turtles: Empire City",
    "The Witcher 4": "The Witcher IV",
    "Trails in the Sky 2nd Chapter": "Trails in the Sky 2nd Chapter",
    "TTK Testing": "Roblox",
    "Yatoro": "Yatoro",
}


def get(params, api=API):
    query = urllib.parse.urlencode({"format": "json", "formatversion": 2, **params})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{api}?{query}", headers=HEADERS), timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == 5:
                raise
            time.sleep(int(error.headers.get("Retry-After") or 0) or 5 * (attempt + 1))  # rate limited: wait


def search(text):
    result = get({"action": "query", "list": "search", "srsearch": text, "srlimit": 1})["query"]["search"]
    return result[0]["title"] if result else None


def picture(title):
    pages = get({"action": "query", "titles": title, "redirects": 1, "prop": "pageimages", "piprop": "thumbnail",
                 "pithumbsize": 480, "pilicense": "any"})["query"]["pages"]
    thumbnail = pages[0].get("thumbnail") if pages else None
    return thumbnail["source"].split("?", 1)[0] if thumbnail else None


def wikidata_picture(title):
    """The image (P18) or logo (P154) of the article's Wikidata item, as a Commons thumbnail: for articles whose
    infobox picture Wikipedia does not hand out (Fortnite, Minecraft, ...)."""
    entities = get({"action": "wbgetentities", "sites": "enwiki", "titles": title, "props": "claims",
                    "redirects": "yes", "formatversion": 1}, WIKIDATA_API).get("entities", {})
    for entity in entities.values():
        for prop in ("P18", "P154"):
            for claim in entity.get("claims", {}).get(prop, []):
                file_name = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
                if not file_name:
                    continue
                pages = get({"action": "query", "titles": f"File:{file_name}", "prop": "imageinfo", "iiprop": "url",
                             "iiurlwidth": 480}, COMMONS_API)["query"]["pages"]
                info = pages[0].get("imageinfo") if pages else None
                if info:
                    return (info[0].get("thumburl") or info[0]["url"]).split("?", 1)[0]
    return None


def query_for(category_id, name, detail):
    if category_id == "user-creation":
        # Mods and maps rarely have an article: the game they were made for stands in
        return detail.split(", ", 1)[1] + " video game"
    if category_id == "performance":
        # The game of the role, not a photo of the actor
        return detail + " video game"
    hint = SEARCH_HINT.get(category_id, "video game")
    return f"{name} {hint}".strip()


def save(images):
    IMAGES.write_text(json.dumps(dict(sorted(images.items())), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    images = {} if args.all or not IMAGES.exists() else json.loads(IMAGES.read_text(encoding="utf-8"))
    categories = json.loads(NOMINEES.read_text(encoding="utf-8"))["categories"]
    for category in categories:
        for name, detail in category["nominees"]:
            if name in images and name not in OVERRIDES:
                continue
            title = OVERRIDES[name] if name in OVERRIDES else search(query_for(category["id"], name, detail))
            url = (picture(title) or wikidata_picture(title)) if title else None
            images[name] = {"title": title, "url": url}
            print(f"{category['id']:18} {name:45} -> {title} {'' if url else '(no picture)'}", flush=True)
            save(images)  # after each name, so a stopped run keeps what it found
            time.sleep(1)
    save(images)
    missing = [name for name, entry in images.items() if not entry["url"]]
    print(f"{len(images)} names, {len(missing)} without a picture: {missing}", file=sys.stderr)


if __name__ == "__main__":
    main()
