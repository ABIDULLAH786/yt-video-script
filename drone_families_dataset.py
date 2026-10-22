#!/usr/bin/env python3
"""
drone_families_dataset.py - collect drone images grouped by VISUAL FAMILY.

Companion to neo_dataset.py. That script targets one model (DJI Neo); this one
targets the seven shape families in list-of-drones-grouped-by-size-and-shape.md
and keeps everything foldered by family, so coverage gaps stay visible instead
of disappearing into one big pile.

The search engines, CLIP filter and phash dedupe are imported from
neo_dataset.py - no point maintaining two copies. What this script adds:

  * a query catalog keyed by family, weighted toward the three known gaps
    (open-frame FPV, heavy-lift hex/octo, fixed-wing/VTOL)
  * a Playwright image-search scraper (`browser`) with no API key and no daily
    cap - this is where the volume comes from
  * per-family YouTube frame extraction with family-specific search terms
  * a coverage report so you can see which families are still thin

WHICH ENGINE ACTUALLY WORKS (measured on this network, Sept 2026):

  bing      ~150 originals/query, plateaus after ~4 scrolls, never blocked. USE THIS.
  yandex     ~70 originals/query, different result pool, never blocked.
  google       0 headless - serves the /sorry/index CAPTCHA to automation.
               Workable via: (a) `browser --engine google --profile-dir DIR`,
               run once with --show-browser to solve the CAPTCHA by hand, then
               reuse the profile headless; (b) the CSE JSON API, 100 q/day free;
               (c) SerpApi, which runs the search on their own IPs.
  openverse  works, CC-licensed only, modest volume.
  ddg        DEAD - the i.js endpoint returns 403 as of Sept 2026.
  reddit     needs a descriptive User-Agent or it 403s.

Bing at ~150/query across 157 queries is ~23,000 candidate images, which is
more than enough before dedupe. Google is a bonus, not the backbone.

Install:
  pip install imagehash pillow yt-dlp requests
  pip install playwright && playwright install chromium   # for `browser`
  (ffmpeg must be on PATH for `frames`)

Pipeline order - filter BEFORE dedupe, so phash time is not spent on images
that are not drones at all:

  python drone_families_dataset.py report
  python drone_families_dataset.py browser --families gap --per-query 150
  python drone_families_dataset.py browser --families gap --engine yandex
  python drone_families_dataset.py scrape  --families gap --engines openverse
  python drone_families_dataset.py frames  --families gap --fps 0.5
  python drone_families_dataset.py filter  --families gap --keep-rejects
  python drone_families_dataset.py dedupe  --families all --src raw/filtered

ALWAYS eyeball <family>__rejected/ after filtering. Measured on 317 f3 images:
the keep/drop split looked entirely reasonable at 254/63, but opening four of
the drops found two genuine cinewhoops in the bin, including a clean DJI Avata
at 0.489. The numbers cannot tell you the filter is working - only looking can.
Rejects run ~8% of a batch, which is small enough to review by hand.

Known residual: CLIP reads a correct-shape drone on a seamless white background
as "a spare part on its own" and drops it. Those studio shots are the least
useful images for a detector anyway, so this is tolerable - but it is why the
rejects folder is worth a scroll rather than a delete.

Note on licensing: search results are ordinary copyrighted photographs. Training
an in-house detector on them is one thing; redistributing the scraped images is
another. The `openverse` engine returns only permissively licensed material if
you need a set you can actually ship.
"""

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus, unquote

# Engines, CLIP filter and dedupe all live in neo_dataset.py, same folder.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import neo_dataset as neo  # noqa: E402


# ------------------------------------------------------------------ catalog
# Structure per family:
#   status   - "covered" or "gap", straight from the coverage analysis
#   note     - why it matters for detection (shows up in `report`)
#   queries  - image-search terms. Many narrow queries beat one broad one:
#              each phrasing surfaces a partly different result pool, and
#              engines start returning filler once real matches run out.
#   videos   - YouTube search terms for the `frames` command. Flight footage
#              gives motion blur, odd angles and real backgrounds that product
#              photography never will.
FAMILIES = {
    # ---------------------------------------------------------------- 1
    "f1_folding_quad": {
        "status": "covered",
        "note": "Folding camera quads, sub-250g to ~1kg. The bulk of real traffic.",
        "queries": [
            "DJI Mavic 4 Pro drone",
            "DJI Mavic 3 Pro flying",
            "DJI Mavic 3 Classic in flight",
            "DJI Air 3S drone flying",
            "DJI Air 3 in flight",
            "DJI Air 2S flying",
            "DJI Mavic Air 2 in flight",
            "DJI Mini 5 Pro drone",
            "DJI Mini 4 Pro flying",
            "DJI Mini 4K drone",
            "DJI Mini 3 Pro in flight",
            "DJI Mini 2 SE flying",
            "DJI Mavic Mini drone",
            "DJI Mavic 2 Pro flying",
            "DJI Mavic 2 Zoom in flight",
            "DJI Phantom 4 Pro flying",
            "DJI Phantom 4 RTK in flight",
            "DJI Phantom 4 V2 drone sky",
            "Potensic Atom 2 drone",
            "Potensic Atom drone flying",
            "Autel EVO II Pro V3 flying",
            "Autel EVO Nano plus drone",
            "Autel EVO Lite plus in flight",
            "Holy Stone HS720 drone",
            "Holy Stone HS175D flying",
            "Holy Stone HS600D drone",
            "Ruko F11 drone flying",
            "SkyRover X1 drone",
            "Parrot Anafi drone flying",
            "Parrot Anafi AI in flight",
            "folding quadcopter drone against sky",
            "consumer camera drone flying low altitude",
        ],
        "videos": [
            "DJI Mavic 4 Pro flight test",
            "DJI Mini 4 Pro footage",
            "DJI Air 3S review flight",
            "DJI Phantom 4 flying footage",
            "Autel EVO II flight test",
        ],
    },
    # ---------------------------------------------------------------- 2
    "f2_micro_selfie": {
        "status": "covered",
        "note": "Palm-sized selfie drones and enclosed-prop micros. Smallest signature.",
        "queries": [
            "DJI Neo drone flying",
            "DJI Neo 2 drone",
            "DJI Flip drone in flight",
            "DJI Flip folded propeller guard",
            "HoverAir X1 flying",
            "HoverAir X1 Pro drone",
            "HoverAir X1 Pro Max in flight",
            "Skydio 2 plus drone flying",
            "Skydio 2 drone in flight",
            "palm sized selfie drone flying",
            "mini drone with propeller guards flying",
            "handheld launch drone takeoff",
        ],
        "videos": [
            "HoverAir X1 Pro flight test",
            "DJI Flip review flight",
            "DJI Neo 2 footage",
        ],
    },
    # ---------------------------------------------------------------- 3
    "f3_ducted_fpv": {
        "status": "gap",
        "note": "Ducted FPV / cinewhoop. Prop guards change the outline completely.",
        "queries": [
            "DJI Avata 2 flying",
            "DJI Avata drone in flight",
            "DJI Avata 360 drone",
            "DJI FPV drone flying",
            "Insta360 Antigravity A1 drone",
            "cinewhoop drone flying",
            "ducted fan quadcopter drone",
            "cinewhoop with prop guards in flight",
            "FPV drone with ducts flying indoor",
            "3 inch cinewhoop flying",
            "prop guard drone flying close up",
            "cinewhoop drone bando flight",
        ],
        "videos": [
            "DJI Avata 2 flight footage",
            "cinewhoop flight test",
            "DJI FPV drone chase footage",
        ],
    },
    # ---------------------------------------------------------------- 4  GAP
    "f4_open_frame_fpv": {
        "status": "gap",
        "note": "Open-frame FPV. Almost no body mass - thin arms and a battery. "
        "The most relevant family on the counter-drone threat side.",
        "queries": [
            "FPV racing drone flying",
            "freestyle FPV quad in flight",
            "5 inch FPV drone flying outdoor",
            "7 inch long range FPV drone",
            "10 inch FPV quad flying",
            "3 inch FPV quad flying",
            "iFlight Nazgul 5 drone",
            "iFlight Nazgul Evoque flying",
            "GEPRC Mark 5 FPV drone",
            "GEPRC Cinelog drone",
            "BetaFPV whoop drone flying",
            "BetaFPV Pavo drone",
            "Betaflight racing quad flying",
            "DIY FPV drone build flying",
            "carbon fiber FPV frame quadcopter",
            "FPV drone racing gate flight",
            "MultiGP racing drone in flight",
            "FPV quad with GoPro flying",
            "long range FPV quad sky",
            "FPV drone silhouette against sky",
            "racing drone close up flying",
            "ELRS FPV quadcopter flying",
            "open frame quadcopter no shell flying",
            "FPV drone bando freestyle",
            "дрон FPV гоночный",  # Russian
            "FPV ドローン レース",  # Japanese
            "穿越机 无人机 飞行",  # Chinese (FPV / racing drone)
        ],
        "videos": [
            "FPV freestyle flight footage",
            "FPV drone racing chase cam",
            "5 inch FPV long range flight",
            "FPV drone flying past camera",
            "drone racing league onboard footage",
        ],
    },
    # ---------------------------------------------------------------- 5
    "f5_enterprise_quad": {
        "status": "covered",
        "note": "Enterprise inspection / public safety. Payload gimbal can read "
        "as a second object under the airframe.",
        "queries": [
            "DJI Matrice 400 drone",
            "DJI Matrice 4E drone flying",
            "DJI Matrice 4T in flight",
            "DJI Matrice 350 RTK flying",
            "DJI Matrice 300 RTK in flight",
            "DJI Matrice 30T drone flying",
            "DJI Mavic 3 Enterprise flying",
            "DJI Mavic 3 Thermal in flight",
            "DJI Mavic 3 Multispectral drone",
            "DJI Dock 3 drone",
            "Autel EVO Max 4T flying",
            "Autel EVO II Dual 640T drone",
            "Skydio X10 drone flying",
            "Skydio X10D in flight",
            "Skydio Dock drone",
            "Parrot Anafi USA flying",
            "BRINC Lemur 2 drone",
            "BRINC Responder drone flying",
            "Teal Black Widow drone",
            "Inspired Flight IF800 Tomcat drone",
            "Inspired Flight IF1200A flying",
            "police drone flying over city",
            "industrial inspection drone with gimbal",
            "search and rescue drone in flight",
        ],
        "videos": [
            "DJI Matrice 400 flight demo",
            "Skydio X10 flight test",
            "public safety drone deployment footage",
            "DJI Dock 3 automated flight",
        ],
    },
    # ---------------------------------------------------------------- 6  GAP
    "f6_heavy_lift": {
        "status": "gap",
        "note": "Heavy-lift hex/octo. Large and usually close up - exactly the "
        "old v9 close-range failure mode.",
        "queries": [
            "DJI Agras T100 spraying drone",
            "DJI Agras T70P drone",
            "DJI Agras T55 flying",
            "DJI Agras T50 spraying",
            "DJI Agras T40 agriculture drone",
            "DJI Agras T30 spraying crops",
            "DJI Agras T25 drone flying",
            "XAG P100 Pro agriculture drone",
            "XAG V40 drone flying",
            "agricultural spraying drone in field",
            "crop spraying drone hexacopter",
            "DJI FlyCart 30 cargo drone",
            "DJI FlyCart 100 delivery drone",
            "heavy lift cargo drone flying",
            "Freefly Alta X drone flying",
            "Freefly Astro drone in flight",
            "DJI Inspire 3 flying",
            "DJI Inspire 2 in flight",
            "Harris Aerial H6 drone",
            "Hylio agriculture drone spraying",
            "octocopter drone flying",
            "hexacopter drone in flight",
            "cinema camera drone heavy lift",
            "large multirotor drone carrying payload",
            "农业植保无人机 飞行",  # Chinese (agricultural drone)
            "drone agricola fumigacion volando",  # Spanish
        ],
        "videos": [
            "DJI Agras T50 spraying footage",
            "agricultural drone spraying field",
            "DJI FlyCart 30 delivery flight",
            "Freefly Alta X cinema drone flight",
            "heavy lift octocopter test flight",
        ],
    },
    # ---------------------------------------------------------------- 7  GAP
    "f7_fixed_wing_vtol": {
        "status": "gap",
        "note": "Fixed-wing and VTOL. CAUTION: the model is currently taught "
        "plane-shaped = not a drone. Read the class-definition note in "
        "the family doc before training on these.",
        "queries": [
            "WingtraOne Gen II drone",
            "WingtraOne tailsitter VTOL takeoff",
            "AgEagle eBee X fixed wing drone",
            "senseFly eBee flying",
            "Quantum Systems Trinity Pro drone",
            "Quantum Systems Trinity F90 flying",
            "Quantum Systems Vector VTOL drone",
            "JOUAV CW-15 VTOL drone",
            "JOUAV CW-25E fixed wing UAV",
            "Autel Dragonfish VTOL drone",
            "DeltaQuad VTOL drone flying",
            "Zipline P1 delivery drone",
            "Zipline P2 drone flying",
            "Wing Alphabet delivery drone flying",
            "Matternet M2 delivery drone",
            "Manna delivery drone flying",
            "fixed wing mapping drone in flight",
            "VTOL quadplane UAV flying",
            "tailsitter VTOL drone transition",
            "flying wing UAV survey drone",
            "hand launched fixed wing drone",
            "fixed wing surveillance UAV sky",
            "vertical takeoff fixed wing drone",
            "belly landing survey drone foam",
        ],
        "videos": [
            "WingtraOne flight demo",
            "eBee X mapping flight",
            "Quantum Systems Trinity flight test",
            "Zipline delivery drone flight",
            "VTOL fixed wing drone transition footage",
        ],
    },
}

GAP_FAMILIES = [k for k, v in FAMILIES.items() if v["status"] == "gap"]


# ------------------------------------------------------------ filter prompts
# Why this is not optional: engines pad results with filler once the real
# matches run out, and at 150 images/query you are well past the honest ones.
# Training on that filler teaches the model that a product box or a channel
# logo is a drone - the same class of error as the office-wall false positives.
#
# CLIP scores an image against sentences. The keep list describes the family's
# actual shape, the drop list describes the filler. Keep-probabilities are
# summed and compared against --threshold.
#
# These are per family on purpose. neo_dataset.py's built-in prompts say "a
# small white quadcopter", which is right for the Neo and would throw away
# almost every open-frame racer (f4) and every fixed-wing airframe (f7) - i.e.
# exactly the gap data this script exists to collect.
KEEP_PROMPTS = {
    "f1_folding_quad": [
        "a folding camera drone flying in the sky",
        "a quadcopter drone with four arms in flight",
        "a small white consumer drone hovering",
        "a person holding a folding camera drone",
    ],
    "f2_micro_selfie": [
        "a tiny palm sized drone",
        "a small drone with propeller guards",
        "a mini drone resting in a person's hand",
        "a small drone hovering close to a person",
    ],
    "f3_ducted_fpv": [
        "an FPV drone with ducted propeller guards",
        "a cinewhoop drone flying",
        "a drone with round prop guards in flight",
        "a small quadcopter with fully enclosed propellers",
    ],
    "f4_open_frame_fpv": [
        "an open frame FPV racing quadcopter",
        "a carbon fibre racing drone with exposed motors",
        "an FPV freestyle quad flying fast",
        "a racing drone with a camera mounted on the front",
        "a quadcopter with thin arms and exposed electronics",
    ],
    "f5_enterprise_quad": [
        "a large industrial inspection drone with a camera gimbal",
        "an enterprise drone with a sensor payload underneath",
        "a public safety drone flying",
        "a professional survey quadcopter in flight",
    ],
    "f6_heavy_lift": [
        "a large agricultural spraying drone",
        "a hexacopter drone with six rotor arms",
        "an octocopter drone with eight rotors",
        "a heavy lift drone carrying a payload",
        "a crop spraying drone flying over a field",
    ],
    "f7_fixed_wing_vtol": [
        "a fixed wing mapping drone",
        "a flying wing unmanned aircraft",
        "a VTOL drone with both wings and rotors",
        "a tailsitter drone taking off vertically",
        "a foam winged survey aircraft on the ground",
    ],
}

# Shared across families. The one that earns its place most is the aerial-photo
# line: queries like "FPV drone footage" return far more pictures TAKEN BY a
# drone than pictures OF one, and those are pure negatives wearing a positive
# label - the single biggest contaminant in this whole catalog.
#
# CAUTION, learned the hard way: an earlier version of this list carried
# "a close up photo of a person's face". It dropped a pilot-in-goggles shot
# with a ducted drone clearly in frame at a score of 0.003. Pilot-with-drone
# photos are among the MOST useful images here - a real airframe at real scale
# in a real environment - so the person prompts below are worded to fire only
# when no drone is present, and PEOPLE_KEEP re-rescues the rest.
DROP_PROMPTS = [
    "an aerial photograph taken from a drone looking down at the ground",
    "a landscape or cityscape photo with no drone in it",
    "a screenshot of text or a user interface",
    "a cartoon, illustration or 3D render",
    "a product box, retail packaging or an accessory kit laid out flat",
    "a company logo or title graphic",
    "a portrait of a person with no drone anywhere in the picture",
    "a remote control transmitter, goggles or phone on their own",
    "a drone battery, propeller or spare part on its own",
    "a chart, diagram or infographic",
]

# Appended to every family's keep list. Scenes that contain a drone but are not
# a clean product shot - these are exactly what a detector needs to see, and
# they are the ones a naive prompt set throws away.
PEOPLE_KEEP = [
    "a person wearing FPV goggles flying a drone outdoors",
    "a drone in flight with a person visible in the background",
    "a pilot launching a drone from their hand",
]


def resolve_families(spec):
    """--families accepts: all | gap | covered | comma-separated keys or numbers."""
    if not spec or spec == "all":
        return list(FAMILIES)
    if spec == "gap":
        return list(GAP_FAMILIES)
    if spec == "covered":
        return [k for k, v in FAMILIES.items() if v["status"] == "covered"]
    out = []
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if token in FAMILIES:
            out.append(token)
            continue
        # allow "4" or "f4" as shorthand
        hit = [k for k in FAMILIES if k.startswith(f"f{token.lstrip('f')}_")]
        if hit:
            out.append(hit[0])
        else:
            print(f"! unknown family: {token}", file=sys.stderr)
    return out


# ------------------------------------------------------ browser engines
# Why a browser at all: the Custom Search JSON API in neo_dataset.py is the
# correct way to talk to Google, but it allows 100 queries/day free at 10
# results each. This catalog is ~157 queries, so the API cannot cover it in a
# day. Driving a real image-results page has no such ceiling.
#
# Measured on this network (Sept 2026), per query:
#   bing    ~150 originals, plateaus after ~4 scrolls, never blocked
#   yandex   ~70 originals, different result pool, never blocked
#   google     0 - serves the /sorry/index CAPTCHA wall to headless Chromium
#
# So bing carries the bulk and google needs one of the escape hatches in
# GOOGLE_HELP below. Keep bing first in --engines.
BROWSER_ENGINES = ("bing", "yandex", "google")

GOOGLE_HELP = """
  Google blocked this request (CAPTCHA / "unusual traffic"). Three ways round it:

   1. Persistent profile - run once with --show-browser --profile-dir ~/.gimg,
      solve the CAPTCHA by hand, then reuse that same --profile-dir headless.
      The clearance cookie persists for days. Needs a display (DISPLAY is set).
   2. CSE API - `scrape --engines google --google-key K --google-cx X`.
      Official and reliable, but 100 queries/day free.
   3. SerpApi - `scrape --engines serpapi --serpapi-key K`. Runs the Google
      search on their infrastructure, so this IP block does not apply.

  Meanwhile `browser --engine bing` needs none of the above and returns ~150
  originals per query, which is where the volume actually is.
"""

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
# Playwright sets navigator.webdriver and drops a few properties a real Chrome
# has. Restoring them is not enough for Google, but it keeps Bing and Yandex
# from degrading to their no-JS layouts.
_STEALTH = """
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
window.chrome={runtime:{}};
"""

_THUMB_HOSTS = (
    "encrypted-tbn",
    "gstatic.com/images",
    "googleusercontent.com/proxy",
    "bing.com/th",
    "bing.net/th",
    "avatars.mds.yandex",
)

# One extractor per engine. Each returns original (not thumbnail) URLs.
_PATTERNS = {
    # Bing embeds the original URL in an HTML-escaped JSON blob per tile.
    "bing": [re.compile(r"murl&quot;:&quot;(https?://[^&]+?)&quot;")],
    # Yandex puts a percent-encoded original in the tile's href.
    "yandex": [re.compile(r"img_url=(https?%3A%2F%2F[^&\"]+)")],
    # Google's embedded result JSON: ["<url>",height,width]
    "google": [
        re.compile(
            r'\["(https?://(?!encrypted-tbn)[^"\\]+?'
            r'\.(?:jpg|jpeg|png|webp))",\s*\d+,\s*\d+\]',
            re.IGNORECASE,
        )
    ],
}

_SEARCH_URLS = {
    "bing": "https://www.bing.com/images/search?q={q}&form=HDRSC2&first=1",
    "yandex": "https://yandex.com/images/search?text={q}",
    # udm=2 is the Images tab; tbs=isz:m drops icon-sized junk.
    "google": "https://www.google.com/search?q={q}&udm=2&hl=en&gl=us&tbs=isz:m",
}

_MORE_BUTTONS = ("See more images", "Show more results", "More results")


def _clean_url(raw):
    """Engines escape URLs differently: JSON \\u, percent-encoding, HTML entities."""
    url = raw.replace("\\u003d", "=").replace("\\u0026", "&").replace("\\/", "/")
    if url.startswith("https%3A") or url.startswith("http%3A"):
        url = unquote(url)
    return html.unescape(url)


def _dismiss_consent(page):
    """EU/UK sessions land on a consent wall before any results render."""
    for sel in (
        "button:has-text('Accept all')",
        "button:has-text('Reject all')",
        "button:has-text('I agree')",
        "#L2AGLb",
        "#bnp_btn_accept",
        "form[action*='consent'] button",
    ):
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1200):
                btn.click()
                page.wait_for_timeout(700)
                return
        except Exception:
            continue


def _is_blocked(page, content):
    return "/sorry/" in page.url or "unusual traffic" in content or (
        "captcha" in page.url.lower()
    )


def _browser_urls(
    engine,
    query,
    per_query,
    headless=True,
    scrolls=6,
    pause=1.1,
    profile_dir=None,
):
    """Scrape one image-search engine for one query. Returns original URLs.

    Set profile_dir to reuse cookies between runs - that is what makes the
    Google path viable after a human has solved the CAPTCHA once.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "    ! playwright missing: pip install playwright "
            "&& playwright install chromium",
            file=sys.stderr,
        )
        return []

    launch_args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    found, seen = [], set()

    with sync_playwright() as pw:
        if profile_dir:
            ctx = pw.chromium.launch_persistent_context(
                str(Path(profile_dir).expanduser()),
                headless=headless,
                args=launch_args,
                user_agent=_UA,
                viewport={"width": 1400, "height": 1000},
                locale="en-US",
            )
            browser = None
        else:
            browser = pw.chromium.launch(headless=headless, args=launch_args)
            ctx = browser.new_context(
                user_agent=_UA,
                viewport={"width": 1400, "height": 1000},
                locale="en-US",
            )
        ctx.add_init_script(_STEALTH)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        try:
            page.goto(
                _SEARCH_URLS[engine].format(q=quote_plus(query)),
                wait_until="domcontentloaded",
                timeout=45000,
            )
            _dismiss_consent(page)
            page.wait_for_timeout(2000)

            if _is_blocked(page, page.content()):
                print(f"    ! {engine} blocked this request", file=sys.stderr)
                if engine == "google":
                    print(GOOGLE_HELP, file=sys.stderr)
                return []

            # Scroll until the unique-URL count stops growing. Bing plateaus
            # around 150 after ~4 passes; scrolling further just burns time.
            stagnant = 0
            for _ in range(scrolls):
                page.mouse.wheel(0, 9000)
                page.wait_for_timeout(int(pause * 1000))
                for label in _MORE_BUTTONS:
                    try:
                        btn = page.get_by_role("button", name=label).first
                        if btn.is_visible(timeout=500):
                            btn.click()
                            page.wait_for_timeout(1200)
                    except Exception:
                        pass
                before = len(seen)
                _harvest(engine, page.content(), seen, found)
                if len(found) >= per_query:
                    break
                stagnant = stagnant + 1 if len(seen) == before else 0
                if stagnant >= 2:
                    break
        except Exception as exc:
            print(f"    ! {engine}: {str(exc)[:160]}", file=sys.stderr)
        finally:
            ctx.close()
            if browser:
                browser.close()
    return found[:per_query]


def _harvest(engine, content, seen, found):
    """Pull originals out of the rendered page into `found`, skipping thumbnails."""
    for pat in _PATTERNS[engine]:
        for raw in pat.findall(content):
            url = _clean_url(raw)
            if url in seen or any(h in url for h in _THUMB_HOSTS):
                continue
            if not url.lower().startswith("http"):
                continue
            seen.add(url)
            found.append(url)


def cmd_browser(args):
    """Browser-driven image search. This is the volume path - no API key, no
    daily cap. Default engine is bing because google blocks headless traffic."""
    out = Path(args.out)
    fams = resolve_families(args.families)
    engines = [e.strip() for e in args.engine.split(",") if e.strip()]
    for e in engines:
        if e not in BROWSER_ENGINES:
            print(f"! unknown browser engine: {e}", file=sys.stderr)
            return
    if not fams:
        print("no families selected", file=sys.stderr)
        return

    total_saved = 0
    for fam in fams:
        meta = FAMILIES[fam]
        print(f"\n=== {fam}  ({meta['status']}) ===", flush=True)
        for qi, query in enumerate(meta["queries"]):
            for engine in engines:
                target = out / fam / engine / f"{qi:02d}_{_slug(query)}"
                target.mkdir(parents=True, exist_ok=True)
                marker = target / ".done"
                if marker.exists() and not args.no_resume:
                    print(f"[{engine}] {query} - done, skipping", flush=True)
                    continue
                print(f"[{engine}] {query}", flush=True)
                urls = _browser_urls(
                    engine,
                    query,
                    args.per_query,
                    headless=not args.show_browser,
                    scrolls=args.scrolls,
                    pause=args.pause,
                    profile_dir=args.profile_dir,
                )
                n = neo._download_urls(urls, target, args.threads, args.timeout)
                total_saved += n
                if urls:
                    marker.touch()
                print(f"    {len(urls)} urls -> {n} saved", flush=True)
                time.sleep(args.delay)  # be a reasonable guest
    print(f"\nDone. {total_saved} images under {out}/ - run `dedupe` next.")


# ------------------------------------------------------------------ scrape
def _slug(text):
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")[:40]


def cmd_scrape(args):
    """Everything except the browser-Google path. Engines come from neo_dataset."""
    out = Path(args.out)
    fams = resolve_families(args.families)
    chosen = [e.strip() for e in args.engines.split(",") if e.strip()]

    needs = {
        "google": (args.google_key and args.google_cx, "--google-key/--google-cx"),
        "brave": (args.brave_key, "--brave-key"),
        "serpapi": (args.serpapi_key, "--serpapi-key"),
        "flickr": (args.flickr_key, "--flickr-key"),
    }
    for eng, (ok, flag) in needs.items():
        if eng in chosen and not ok:
            print(f"skipping {eng}: needs {flag}", file=sys.stderr)
            chosen.remove(eng)
    if not chosen:
        print("no usable engines selected", file=sys.stderr)
        return

    for fam in fams:
        meta = FAMILIES[fam]
        print(f"\n=== {fam}  ({meta['status']}) ===", flush=True)
        for qi, query in enumerate(meta["queries"]):
            for engine in chosen:
                target = out / fam / engine / f"{qi:02d}_{_slug(query)}"
                target.mkdir(parents=True, exist_ok=True)
                marker = target / ".done"
                if marker.exists() and not args.no_resume:
                    print(f"[{engine}] {query} - done, skipping", flush=True)
                    continue
                print(f"[{engine}] {query}", flush=True)
                try:
                    urls = _dispatch(engine, query, args)
                    if urls is None:  # engine wrote its own files (bing/icrawler)
                        marker.touch()
                        continue
                    n = neo._download_urls(urls, target, args.threads, args.timeout)
                    if urls:
                        marker.touch()
                    print(f"    {len(urls)} urls -> {n} saved", flush=True)
                except Exception as exc:  # one dead engine shouldn't kill the run
                    print(f"    ! failed: {str(exc)[:160]}", file=sys.stderr)

    print(f"\nDone. Files under {out}/ - run `filter` then `dedupe`.")


def _dispatch(engine, query, args):
    """Returns a URL list, or None if the engine saved files itself."""
    t = args.timeout
    n = args.per_query
    if engine == "ddg":
        return neo._ddg_urls(query, n, t)
    if engine == "google":
        return neo._google_urls(query, n, t, args.google_key, args.google_cx)
    if engine == "brave":
        return neo._brave_urls(query, n, t, args.brave_key)
    if engine == "serpapi":
        return neo._serpapi_urls(query, n, t, args.serpapi_key)
    if engine == "reddit":
        return neo._reddit_urls(query, n, t)
    if engine == "flickr":
        return neo._flickr_urls(query, n, t, args.flickr_key)
    if engine == "openverse":
        return neo._openverse_urls(query, n, t)
    if engine == "bing":
        from icrawler.builtin import BingImageCrawler

        target = Path(args.out)
        BingImageCrawler(
            downloader_threads=args.threads,
            storage={"root_dir": str(target)},
            log_level=50,
        ).crawl(keyword=query, max_num=n, min_size=(args.min_side, args.min_side))
        return None
    raise ValueError(f"unknown engine {engine}")


# ------------------------------------------------------------------ frames
def cmd_frames(args):
    """YouTube -> frames, per family. Flight footage is where the motion blur,
    odd angles and real backgrounds come from; product photos have none."""
    out = Path(args.out)
    tmp = Path(args.workdir)
    tmp.mkdir(parents=True, exist_ok=True)
    fams = resolve_families(args.families)

    for fam in fams:
        meta = FAMILIES[fam]
        fam_out = out / fam
        fam_out.mkdir(parents=True, exist_ok=True)
        print(f"\n=== {fam}  ({meta['status']}) ===", flush=True)

        for i, term in enumerate(meta["videos"]):
            marker = fam_out / f".done_{i:02d}"
            if marker.exists() and not args.no_resume:
                print(f"[dl] {term} - done, skipping", flush=True)
                continue
            vid_dir = tmp / fam / f"v{i:03d}"
            vid_dir.mkdir(parents=True, exist_ok=True)
            print(f"[dl] {term}", flush=True)

            cmd = ["yt-dlp"]
            if args.js_runtime:
                # YouTube needs a JS runtime since yt-dlp 2025.11.12; without one
                # it falls back to android_vr and YouTube answers 403.
                cmd += ["--js-runtimes", args.js_runtime]
            if args.ffmpeg_location:
                cmd += ["--ffmpeg-location", args.ffmpeg_location]
            cmd += [
                # video-only first (no audio needed, no merge step), then
                # progressive, then anything. A strict [ext=mp4] filter can
                # match nothing at all on clients that only serve HLS.
                "-f",
                "bv*[height<=1080]/b[height<=1080]/bv*/b",
                "--match-filter",
                f"duration < {args.max_duration}",
                "--yes-playlist",
                "-o",
                str(vid_dir / "%(id)s.%(ext)s"),
                f"ytsearch{args.search_n}:{term}",
            ]
            dl = subprocess.run(cmd, capture_output=True, text=True)
            if dl.returncode != 0:
                print(
                    f"    ! download failed: {dl.stderr.strip()[:200]}",
                    file=sys.stderr,
                )
                continue

            for video in sorted(vid_dir.glob("*")):
                if video.suffix.lower() not in (
                    ".mp4",
                    ".mkv",
                    ".webm",
                    ".m4v",
                    ".ts",
                    ".mov",
                ):
                    continue
                stem = f"{i:02d}_{video.stem}"
                print(f"    [frames] {video.name}", flush=True)
                subprocess.run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-i",
                        str(video),
                        "-vf",
                        f"fps={args.fps},scale='min({args.max_side},iw)':-2",
                        "-q:v",
                        "2",
                        str(fam_out / f"{stem}_%05d.jpg"),
                    ],
                    check=False,
                )
                if not args.keep_video:
                    video.unlink(missing_ok=True)
            marker.touch()

    if not args.keep_video:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nDone. Frames under {out}/ - run `dedupe` next.")


# ------------------------------------------------------------------ filter
def cmd_filter(args):
    """CLIP relevance gate, per family. Run this BETWEEN browser/frames and
    dedupe: no point spending phash time on images that are not drones at all.

    This is a coarse pass - it answers "is a drone plausibly in this picture",
    not "where". Labelling still has to happen afterwards.
    """
    import torch
    from PIL import Image
    from transformers import CLIPModel, CLIPProcessor

    Image.MAX_IMAGE_PIXELS = None
    fams = resolve_families(args.families)
    src_root, out_root = Path(args.src), Path(args.out)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"loading CLIP on {device}", flush=True)
    model = CLIPModel.from_pretrained(args.model).to(device).eval()
    proc = CLIPProcessor.from_pretrained(args.model)

    grand = {"kept": 0, "dropped": 0, "broken": 0}
    for fam in fams:
        src = src_root / fam
        if not src.exists():
            print(f"[skip] {fam}: nothing at {src}")
            continue
        keep_prompts = KEEP_PROMPTS[fam] + PEOPLE_KEEP
        prompts = keep_prompts + DROP_PROMPTS
        n_keep = len(keep_prompts)
        out = out_root / fam
        out.mkdir(parents=True, exist_ok=True)

        files = sorted(p for p in src.rglob("*") if p.suffix.lower() in IMG_EXTS)
        print(f"\n=== {fam} === scoring {len(files)} images", flush=True)

        kept = dropped = broken = 0
        batch, paths, rejects = [], [], []

        def flush():
            nonlocal kept, dropped
            if not batch:
                return
            inputs = proc(
                text=prompts, images=batch, return_tensors="pt", padding=True
            ).to(device)
            with torch.no_grad():
                probs = model(**inputs).logits_per_image.softmax(dim=1)
            for path, row in zip(paths, probs):
                score = row[:n_keep].sum().item()
                if score >= args.threshold:
                    kept += 1
                    shutil.copy2(path, out / f"{fam}_{kept:06d}{path.suffix.lower()}")
                else:
                    dropped += 1
                    rejects.append((score, path))
            batch.clear()
            paths.clear()

        for n, path in enumerate(files, 1):
            try:
                batch.append(Image.open(path).convert("RGB"))
                paths.append(path)
            except Exception:
                broken += 1
                continue
            if len(batch) >= args.batch:
                flush()
                print(f"  {n}/{len(files)} -> kept {kept}", flush=True)
        flush()

        print(f"  kept {kept} | dropped {dropped} | unreadable {broken}")
        if args.show_rejects and rejects:
            print("  lowest-scoring drops (check these to tune --threshold):")
            for score, path in sorted(rejects)[: args.show_rejects]:
                print(f"    {score:.3f}  {path.name}")
        if args.keep_rejects:
            rej_dir = out_root / f"{fam}__rejected"
            rej_dir.mkdir(parents=True, exist_ok=True)
            for score, path in rejects:
                shutil.copy2(path, rej_dir / f"{score:.3f}_{path.name}")
            print(f"  rejects copied to {rej_dir}/ for eyeballing")

        grand["kept"] += kept
        grand["dropped"] += dropped
        grand["broken"] += broken

    print(
        f"\nTOTAL kept {grand['kept']} | dropped {grand['dropped']} "
        f"| unreadable {grand['broken']}"
    )
    print(f"filtered set: {out_root}/  (run `dedupe` on this next)")


# ------------------------------------------------------------------ dedupe
def cmd_dedupe(args):
    """Per-family phash dedupe, so a duplicate in f4 never silently removes a
    legitimately similar frame in f6. Delegates to neo_dataset.cmd_dedupe."""
    fams = resolve_families(args.families)
    src_root, out_root = Path(args.src), Path(args.out)

    for fam in fams:
        src = src_root / fam
        if not src.exists():
            print(f"[skip] {fam}: nothing at {src}")
            continue
        print(f"\n=== {fam} ===", flush=True)
        sub = argparse.Namespace(
            src=str(src),
            out=str(out_root / fam),
            min_side=args.min_side,
            hash_size=args.hash_size,
            threshold=args.threshold,
        )
        neo.cmd_dedupe(sub)


# ------------------------------------------------------------------ report
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _count(path):
    if not path.exists():
        return 0
    return sum(1 for p in path.rglob("*") if p.suffix.lower() in IMG_EXTS)


def cmd_report(args):
    """Coverage table: how many images each family actually has on disk."""
    raw, ds = Path(args.raw), Path(args.dataset)
    rows = []
    for fam, meta in FAMILIES.items():
        rows.append(
            (
                fam,
                meta["status"],
                len(meta["queries"]),
                len(meta["videos"]),
                _count(raw / fam),
                _count(ds / fam),
            )
        )

    w = max(len(r[0]) for r in rows) + 2
    print(f"\n{'family':<{w}}{'status':<10}{'queries':>8}{'videos':>8}"
          f"{'raw':>10}{'clean':>10}")
    print("-" * (w + 46))
    for fam, status, nq, nv, nraw, nclean in rows:
        flag = " <- GAP" if status == "gap" and nclean < args.target else ""
        print(f"{fam:<{w}}{status:<10}{nq:>8}{nv:>8}{nraw:>10}{nclean:>10}{flag}")
    print("-" * (w + 46))
    print(f"{'TOTAL':<{w}}{'':<10}{sum(r[2] for r in rows):>8}"
          f"{sum(r[3] for r in rows):>8}{sum(r[4] for r in rows):>10}"
          f"{sum(r[5] for r in rows):>10}")

    print("\nNotes:")
    for fam, meta in FAMILIES.items():
        if meta["status"] == "gap":
            print(f"  {fam}: {meta['note']}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                [
                    dict(zip(("family", "status", "queries", "videos", "raw", "clean"), r))
                    for r in rows
                ],
                indent=2,
            )
        )
        print(f"\nwrote {args.json}")


# --------------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(
        description="Drone dataset collector, grouped by visual family",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="families: all | gap | covered | " + " | ".join(FAMILIES),
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p, default_out):
        p.add_argument("--families", default="gap", help="all|gap|covered|f4,f6,...")
        p.add_argument("--out", default=default_out)
        p.add_argument("--per-query", type=int, default=120)
        p.add_argument("--threads", type=int, default=8)
        p.add_argument("--timeout", type=int, default=20)
        p.add_argument("--min-side", type=int, default=200)
        p.add_argument("--no-resume", action="store_true")

    # ---- browser (bing / yandex / google)
    g = sub.add_parser(
        "browser",
        help="image search via Playwright - the volume path, no key, no cap",
    )
    add_common(g, "raw/families")
    g.add_argument(
        "--engine",
        default="bing",
        help="bing (default, ~150/query), yandex (~70/query, different pool), "
        "google (needs --profile-dir, see --help-google). Comma separated.",
    )
    g.add_argument(
        "--profile-dir",
        default=None,
        help="persistent browser profile. Run once with --show-browser to "
        "solve a CAPTCHA by hand, then reuse it headless. Required for google.",
    )
    g.add_argument("--show-browser", action="store_true", help="run headed")
    g.add_argument("--scrolls", type=int, default=6, help="max scroll passes")
    g.add_argument("--pause", type=float, default=1.1, help="seconds per scroll step")
    g.add_argument("--delay", type=float, default=1.5, help="seconds between queries")
    g.set_defaults(func=cmd_browser)

    # ---- scrape (all other engines)
    s = sub.add_parser("scrape", help="the non-browser engines from neo_dataset")
    add_common(s, "raw/families")
    s.add_argument(
        "--engines",
        default="openverse",
        help="openverse, google (CSE API), brave, serpapi, flickr, reddit, "
        "bing (icrawler), ddg - comma separated. NB ddg is dead (403) and "
        "reddit 403s without a good UA; for volume use `browser` instead.",
    )
    s.add_argument("--google-key", default=os.environ.get("GOOGLE_API_KEY", ""))
    s.add_argument("--google-cx", default=os.environ.get("GOOGLE_CX", ""))
    s.add_argument("--brave-key", default=os.environ.get("BRAVE_API_KEY", ""))
    s.add_argument("--serpapi-key", default=os.environ.get("SERPAPI_KEY", ""))
    s.add_argument("--flickr-key", default=os.environ.get("FLICKR_API_KEY", ""))
    s.set_defaults(func=cmd_scrape)

    # ---- frames
    f = sub.add_parser("frames", help="YouTube -> frames, per family")
    f.add_argument("--families", default="gap")
    f.add_argument("--out", default="raw/families")
    f.add_argument("--workdir", default="_videos")
    f.add_argument("--search-n", type=int, default=8, help="videos per search term")
    f.add_argument("--fps", type=float, default=0.5)
    f.add_argument("--max-side", type=int, default=1280)
    f.add_argument(
        "--max-duration",
        type=int,
        default=1200,
        help="seconds; skips hour-long compilations that blow up the frame count",
    )
    f.add_argument("--keep-video", action="store_true")
    f.add_argument("--no-resume", action="store_true")
    f.add_argument(
        "--js-runtime",
        default="node",
        help="JS runtime for yt-dlp: node, deno, bun. Empty string to omit.",
    )
    f.add_argument("--ffmpeg-location", default="", help="empty = rely on PATH")
    f.set_defaults(func=cmd_frames)

    # ---- filter
    fi = sub.add_parser(
        "filter",
        help="CLIP relevance gate with per-family prompts - run before dedupe",
    )
    fi.add_argument("--families", default="all")
    fi.add_argument("--src", default="raw/families")
    fi.add_argument("--out", default="raw/filtered")
    fi.add_argument("--model", default="openai/clip-vit-base-patch32")
    fi.add_argument(
        "--threshold",
        type=float,
        default=0.35,
        help="summed keep-probability needed to survive. Default 0.35 is "
        "deliberately permissive: a wrongly-dropped real drone costs more than "
        "a bit of junk that dedupe and labelling will catch anyway. 0.5 was "
        "measured throwing away a clean DJI Avata at 0.489. Always sanity-check "
        "with --keep-rejects before trusting a higher value.",
    )
    fi.add_argument("--batch", type=int, default=32)
    fi.add_argument(
        "--show-rejects",
        type=int,
        default=10,
        help="print the N lowest-scoring drops so you can sanity-check them",
    )
    fi.add_argument(
        "--keep-rejects",
        action="store_true",
        help="copy dropped images to <family>__rejected/ named by score, so you "
        "can see what the threshold is actually throwing away",
    )
    fi.set_defaults(func=cmd_filter)

    # ---- dedupe
    d = sub.add_parser("dedupe", help="per-family phash dedupe + size filter")
    d.add_argument("--families", default="all")
    d.add_argument("--src", default="raw/families")
    d.add_argument("--out", default="dataset/families")
    d.add_argument("--min-side", type=int, default=224)
    d.add_argument("--hash-size", type=int, default=8)
    d.add_argument("--threshold", type=int, default=5)
    d.set_defaults(func=cmd_dedupe)

    # ---- report
    r = sub.add_parser("report", help="coverage table per family")
    r.add_argument("--raw", default="raw/families")
    r.add_argument("--dataset", default="dataset/families")
    r.add_argument("--target", type=int, default=800, help="per-family goal")
    r.add_argument("--json", help="also write the table as JSON")
    r.set_defaults(func=cmd_report)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
