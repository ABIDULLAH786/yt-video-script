#!/usr/bin/env python3
"""
neo_dataset.py - collect a DJI Neo image dataset.

Three subcommands:
  scrape  : pull images from Bing/Google/Baidu image search (icrawler)
  frames  : download YouTube videos and cut them into frames (yt-dlp + ffmpeg)
  dedupe  : perceptual-hash dedupe + drop junk-sized images

Install:
  pip install icrawler imagehash pillow yt-dlp
  (ffmpeg must be on PATH for `frames`)

Typical run:
  python neo_dataset.py scrape --out raw/search --per-query 400
  python neo_dataset.py frames --urls videos.txt --out raw/frames --fps 1
  python neo_dataset.py dedupe --src raw --out dataset --min-side 224
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- queries
# Search engines repeat themselves fast. Many narrow queries beat one broad
# query: each phrasing surfaces a partly different result pool.
QUERIES = [
    "DJI Neo",
    "DJI Neo drone",
    "DJI Neo flying",
    "DJI Neo in flight",
    "DJI Neo palm takeoff",
    "DJI Neo hand launch",
    "DJI Neo selfie drone",
    "DJI Neo review",
    "DJI Neo unboxing",
    "DJI Neo propeller guard",
    "DJI Neo top view",
    "DJI Neo bottom view",
    "DJI Neo side view",
    "DJI Neo size comparison",
    "DJI Neo vs Mini 4K",
    "DJI Neo outdoors",
    "DJI Neo beach",
    "DJI Neo mountain",
    "DJI Neo night",
    "DJI Neo sky",
    "DJI Neo drone photo",
    "DJI Neo mini drone",
    "DJI Neo 4K drone",
    "DJI Neo combo kit",
    "DJI Neo controller",
    "DJI Neo battery",
    "DJI Neo landing",
    "DJI Neo hovering",
    "DJI Neo close up",
    "DJI Neo camera drone 135g",
    "دي جي اي نيو درون",  # Arabic
    "DJI Neo 无人机",  # Chinese
    "DJI Neo ドローン",  # Japanese
    "DJI Neo dron",  # Spanish/Polish
    "DJI Neo дрон",  # Russian
]


# ---------------------------------------------------------------- scrape
def _download_urls(urls, target, threads, timeout, min_bytes=8000):
    """Fetch a list of image URLs into target. Shared by the ddg/google engines."""
    from concurrent.futures import ThreadPoolExecutor
    import requests

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    counter = [0]

    def fetch(url):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code != 200 or len(r.content) < min_bytes:
                return
            ctype = r.headers.get("content-type", "")
            if "image" not in ctype:
                return
            ext = ".png" if "png" in ctype else ".jpg"
            counter[0] += 1
            (target / f"{counter[0]:04d}{ext}").write_bytes(r.content)
        except Exception:
            pass  # dead hosts are the norm in search results

    with ThreadPoolExecutor(max_workers=threads) as pool:
        list(pool.map(fetch, urls))
    return counter[0]


def _ddg_urls(query, per_query, timeout):
    """DuckDuckGo image search over plain requests.

    The `ddgs` package uses a Rust HTTP client that some networks block even
    when ordinary requests traffic passes, so this talks to the endpoint
    directly: fetch a vqd token from the search page, then page through i.js.
    """
    import re
    import requests

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    s = requests.Session()
    s.headers.update(headers)

    try:
        page = s.post("https://duckduckgo.com/", data={"q": query}, timeout=timeout)
        m = re.search(r'vqd=["\']?([\d-]+)["\']?', page.text)
        if not m:
            print("    ! no vqd token", file=sys.stderr)
            return []
        vqd = m.group(1)
    except Exception as exc:
        print(f"    ! token fetch failed: {exc}", file=sys.stderr)
        return []

    urls, offset = [], 0
    while len(urls) < per_query:
        try:
            r = s.get(
                "https://duckduckgo.com/i.js",
                params={
                    "l": "us-en",
                    "o": "json",
                    "q": query,
                    "vqd": vqd,
                    "f": ",,,",
                    "p": "1",
                    "s": offset,
                },
                headers={**headers, "Referer": "https://duckduckgo.com/"},
                timeout=timeout,
            )
            data = r.json()
        except Exception as exc:
            print(f"    ! page {offset} failed: {exc}", file=sys.stderr)
            break
        results = data.get("results", [])
        if not results:
            break
        urls += [it["image"] for it in results if it.get("image")]
        if not data.get("next"):
            break
        offset += 100
    return urls[:per_query]


def _google_urls(query, per_query, timeout, key, cx):
    """Google Programmable Search JSON API.

    Free tier is 100 queries/day and 10 results per query, so roughly 1000
    images/day across all queries. Needs an API key and a search-engine id
    with 'image search' enabled.
    """
    import requests

    urls = []
    for start in range(1, min(per_query, 100), 10):
        try:
            r = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "key": key,
                    "cx": cx,
                    "q": query,
                    "searchType": "image",
                    "num": 10,
                    "start": start,
                    "safe": "off",
                },
                timeout=timeout,
            )
            data = r.json()
        except Exception as exc:
            print(f"    ! request failed: {exc}", file=sys.stderr)
            break
        if "error" in data:
            print(f"    ! api: {data['error'].get('message')}", file=sys.stderr)
            break
        items = data.get("items", [])
        if not items:
            break
        urls += [it["link"] for it in items if it.get("link")]
    return urls[:per_query]


def _serpapi_urls(query, per_query, timeout, key):
    """SerpApi Google Images. Runs the Google search on their infrastructure,
    so a network that blocks google.com directly is not an obstacle here.
    One credit per search regardless of how many results come back; ijn pages
    through in blocks of ~100."""
    import requests

    out = []
    for ijn in range((per_query // 100) + 1):
        try:
            r = requests.get(
                "https://serpapi.com/search.json",
                params={
                    "engine": "google_images",
                    "q": query,
                    "ijn": ijn,
                    "api_key": key,
                    "hl": "en",
                    "gl": "us",
                    "safe": "off",
                },
                timeout=timeout,
            )
            data = r.json()
        except Exception as exc:
            print(f"    ! request failed: {exc}", file=sys.stderr)
            break
        if "error" in data:
            print(f"    ! api: {data['error']}", file=sys.stderr)
            break
        results = data.get("images_results", [])
        if not results:
            break
        out += [it["original"] for it in results if it.get("original")]
        if len(out) >= per_query:
            break
    return out[:per_query]


def _brave_urls(query, per_query, timeout, token):
    """Brave Search image API. Free tier, and unlike Google's Custom Search
    it is still open to new signups. Key from api.search.brave.com."""
    import requests

    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/images/search",
            params={"q": query, "count": min(per_query, 100), "safesearch": "off"},
            headers={"Accept": "application/json", "X-Subscription-Token": token},
            timeout=timeout,
        )
        data = r.json()
    except Exception as exc:
        print(f"    ! request failed: {exc}", file=sys.stderr)
        return []
    if "results" not in data:
        print(f"    ! api: {str(data)[:160]}", file=sys.stderr)
        return []
    out = []
    for it in data["results"]:
        url = (it.get("properties") or {}).get("url") or (
            it.get("thumbnail") or {}
        ).get("src")
        if url:
            out.append(url)
    return out[:per_query]


REDDIT_SUBS = ["dji", "drones", "djineo", "dronephotography", "Multicopter"]


def _reddit_urls(query, per_query, timeout):
    """Reddit search. No key needed, and the payoff here is that these are
    real user photos in real environments rather than studio product shots."""
    import requests

    headers = {"User-Agent": "neo-dataset/1.0 (research)"}
    out = []
    for sub in REDDIT_SUBS:
        after = None
        while len(out) < per_query:
            try:
                r = requests.get(
                    f"https://www.reddit.com/r/{sub}/search.json",
                    params={
                        "q": query,
                        "restrict_sr": 1,
                        "limit": 100,
                        "sort": "relevance",
                        "t": "all",
                        "after": after,
                    },
                    headers=headers,
                    timeout=timeout,
                )
                data = r.json()
            except Exception as exc:
                print(f"    ! r/{sub}: {exc}", file=sys.stderr)
                break
            children = data.get("data", {}).get("children", [])
            if not children:
                break
            for c in children:
                d = c.get("data", {})
                url = d.get("url_overridden_by_dest") or d.get("url", "")
                if any(url.lower().endswith(e) for e in (".jpg", ".jpeg", ".png")):
                    out.append(url)
                # galleries carry several images per post
                for item in (d.get("media_metadata") or {}).values():
                    src = (item.get("s") or {}).get("u")
                    if src:
                        out.append(src.replace("&amp;", "&"))
            after = data.get("data", {}).get("after")
            if not after:
                break
    return out[:per_query]


def _flickr_urls(query, per_query, timeout, key):
    """Flickr photo search. Free key, instant signup, and again these are
    real photographs rather than catalogue renders."""
    import requests

    out = []
    for page in range(1, (per_query // 100) + 2):
        try:
            r = requests.get(
                "https://www.flickr.com/services/rest/",
                params={
                    "method": "flickr.photos.search",
                    "api_key": key,
                    "text": query,
                    "format": "json",
                    "nojsoncallback": 1,
                    "per_page": 100,
                    "page": page,
                    "sort": "relevance",
                    "content_type": 1,
                    "media": "photos",
                },
                timeout=timeout,
            )
            data = r.json()
        except Exception as exc:
            print(f"    ! request failed: {exc}", file=sys.stderr)
            break
        if data.get("stat") != "ok":
            print(f"    ! api: {data.get('message')}", file=sys.stderr)
            break
        photos = data.get("photos", {}).get("photo", [])
        if not photos:
            break
        for p in photos:
            out.append(
                f"https://live.staticflickr.com/{p['server']}/"
                f"{p['id']}_{p['secret']}_b.jpg"
            )
    return out[:per_query]


def _openverse_urls(query, per_query, timeout):
    """Openverse (CC-licensed images). No key, and licensing is explicit."""
    import requests

    out = []
    for page in range(1, (per_query // 20) + 2):
        try:
            r = requests.get(
                "https://api.openverse.org/v1/images/",
                params={"q": query, "page_size": 20, "page": page},
                headers={"User-Agent": "neo-dataset/1.0"},
                timeout=timeout,
            )
            data = r.json()
        except Exception as exc:
            print(f"    ! request failed: {exc}", file=sys.stderr)
            break
        results = data.get("results", [])
        if not results:
            break
        out += [it["url"] for it in results if it.get("url")]
    return out[:per_query]


def cmd_scrape(args):
    chosen = [e.strip() for e in args.engines.split(",") if e.strip()]
    out = Path(args.out)

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

    for qi, query in enumerate(QUERIES):
        for engine in chosen:
            target = out / engine / f"{qi:02d}_{query.replace(' ', '_')[:40]}"
            target.mkdir(parents=True, exist_ok=True)
            marker = target / ".done"
            if marker.exists() and not args.no_resume:
                print(f"[{engine}] {query} - already done, skipping", flush=True)
                continue
            print(f"[{engine}] {query}", flush=True)
            try:
                if engine == "ddg":
                    urls = _ddg_urls(query, args.per_query, args.timeout)
                elif engine == "google":
                    urls = _google_urls(
                        query,
                        args.per_query,
                        args.timeout,
                        args.google_key,
                        args.google_cx,
                    )
                elif engine == "brave":
                    urls = _brave_urls(
                        query, args.per_query, args.timeout, args.brave_key
                    )
                elif engine == "serpapi":
                    urls = _serpapi_urls(
                        query, args.per_query, args.timeout, args.serpapi_key
                    )
                elif engine == "reddit":
                    urls = _reddit_urls(query, args.per_query, args.timeout)
                elif engine == "flickr":
                    urls = _flickr_urls(
                        query, args.per_query, args.timeout, args.flickr_key
                    )
                elif engine == "openverse":
                    urls = _openverse_urls(query, args.per_query, args.timeout)
                elif engine == "bing":
                    from icrawler.builtin import BingImageCrawler

                    BingImageCrawler(
                        downloader_threads=args.threads,
                        storage={"root_dir": str(target)},
                        log_level=50,
                    ).crawl(
                        keyword=query,
                        max_num=args.per_query,
                        min_size=(args.min_side, args.min_side),
                    )
                    marker.touch()
                    print(f"    done", flush=True)
                    continue
                else:
                    print(f"    ! unknown engine {engine}", file=sys.stderr)
                    continue

                n = _download_urls(urls, target, args.threads, args.timeout)
                # only mark complete when the search itself returned something,
                # so a transient network failure gets retried on the next run
                if urls:
                    marker.touch()
                print(f"    {len(urls)} urls -> {n} saved", flush=True)
            except Exception as exc:  # one dead engine shouldn't kill the run
                print(f"    ! failed: {exc}", file=sys.stderr)

    print(f"\nDone. Files under {out}/ - run `filter` then `dedupe`.")


# ---------------------------------------------------------------- frames
def cmd_frames(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(args.workdir)
    tmp.mkdir(parents=True, exist_ok=True)

    if args.urls:
        urls = [
            u.strip()
            for u in Path(args.urls).read_text().splitlines()
            if u.strip() and not u.startswith("#")
        ]
    else:
        # let yt-dlp do the searching: ytsearchN:<query>
        urls = [
            f"ytsearch{args.search_n}:{q}"
            for q in [
                "DJI Neo review",
                "DJI Neo flight test",
                "DJI Neo unboxing",
                "DJI Neo footage",
                "DJI Neo hands on",
                "DJI Neo vs",
            ]
        ]

    for i, url in enumerate(urls):
        vid_dir = tmp / f"v{i:03d}"
        vid_dir.mkdir(exist_ok=True)
        print(f"[dl] {url}", flush=True)
        cmd = ["yt-dlp"]
        if args.js_runtime:
            # YouTube needs a JS runtime since yt-dlp 2025.11.12; without one
            # it falls back to android_vr and YouTube answers 403.
            cmd += ["--js-runtimes", args.js_runtime]
        if args.ffmpeg_location:
            cmd += ["--ffmpeg-location", args.ffmpeg_location]
        cmd += [
            # video-only first (no audio needed for frames, and no merge step),
            # then progressive, then anything. Some clients only serve HLS, so
            # a strict [ext=mp4] filter can match nothing at all.
            "-f",
            "bv*[height<=1080]/b[height<=1080]/bv*/b",
            "--no-playlist" if not url.startswith("ytsearch") else "--yes-playlist",
            "-o",
            str(vid_dir / "%(id)s.%(ext)s"),
            url,
        ]
        dl = subprocess.run(cmd, capture_output=True, text=True)
        if dl.returncode != 0:
            print(f"    ! download failed: {dl.stderr.strip()[:200]}", file=sys.stderr)
            continue

        for video in vid_dir.glob("*"):
            if video.suffix.lower() not in (
                ".mp4",
                ".mkv",
                ".webm",
                ".m4v",
                ".ts",
                ".mov",
            ):
                continue
            stem = f"{i:03d}_{video.stem}"
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
                    str(out / f"{stem}_%05d.jpg"),
                ],
                check=False,
            )
            if not args.keep_video:
                video.unlink(missing_ok=True)

    if not args.keep_video:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nDone. Frames in {out}/ - run `dedupe` next.")


# ---------------------------------------------------------------- filter
PROMPTS_KEEP = [
    "a photo of a small white quadcopter drone",
    "a drone flying in the sky",
    "a person holding a small drone",
    "a drone with propeller guards",
]
PROMPTS_DROP = [
    "a photo of a person",
    "a product photo of medicine or packaging",
    "a screenshot of text",
    "a building or landscape",
    "a cartoon or movie poster",
    "an unrelated consumer product",
]


def cmd_filter(args):
    """Keep only images CLIP thinks actually contain a drone. Search results
    are heavily contaminated once the engine exhausts real matches."""
    import torch
    from PIL import Image
    from transformers import CLIPModel, CLIPProcessor

    src, out = Path(args.src), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"loading CLIP on {device}")
    model = CLIPModel.from_pretrained(args.model).to(device).eval()
    proc = CLIPProcessor.from_pretrained(args.model)

    prompts = PROMPTS_KEEP + PROMPTS_DROP
    n_keep = len(PROMPTS_KEEP)

    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    files = sorted(p for p in src.rglob("*") if p.suffix.lower() in exts)
    print(f"scoring {len(files)} images")

    kept = dropped = broken = 0
    batch, paths = [], []

    def flush():
        nonlocal kept, dropped
        if not batch:
            return
        inputs = proc(text=prompts, images=batch, return_tensors="pt", padding=True).to(
            device
        )
        with torch.no_grad():
            probs = model(**inputs).logits_per_image.softmax(dim=1)
        for path, row in zip(paths, probs):
            score = row[:n_keep].sum().item()
            if score >= args.threshold:
                kept += 1
                shutil.copy2(path, out / f"k_{kept:06d}{path.suffix.lower()}")
            else:
                dropped += 1
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

    print(f"\nkept {kept} | dropped {dropped} | unreadable {broken}")
    print(f"relevant set: {out}/  (run dedupe on this next)")


# ---------------------------------------------------------------- dedupe
def cmd_dedupe(args):
    import imagehash
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    src, out = Path(args.src), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    files = sorted(p for p in src.rglob("*") if p.suffix.lower() in exts)
    print(f"scanning {len(files)} files")

    seen, kept, small, broken, dup = {}, 0, 0, 0, 0
    for n, path in enumerate(files, 1):
        try:
            with Image.open(path) as im:
                im.verify()
            with Image.open(path) as im:
                im = im.convert("RGB")
                if min(im.size) < args.min_side:
                    small += 1
                    continue
                h = imagehash.phash(im, hash_size=args.hash_size)
        except Exception:
            broken += 1
            continue

        # near-duplicate check against everything kept so far
        hit = None
        for known in seen:
            if h - known <= args.threshold:
                hit = known
                break
        if hit is not None:
            dup += 1
            continue

        seen[h] = path
        kept += 1
        shutil.copy2(path, out / f"neo_{kept:06d}{path.suffix.lower()}")
        if n % 500 == 0:
            print(f"  {n}/{len(files)} -> kept {kept}", flush=True)

    print(f"\nkept {kept} | duplicates {dup} | too small {small} | unreadable {broken}")
    print(f"clean set: {out}/")


# ---------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(description="DJI Neo dataset collector")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scrape", help="image-search scraping")
    s.add_argument("--out", default="raw/search")
    s.add_argument(
        "--engines",
        default="serpapi,reddit,openverse,bing",
        help="any of: serpapi, reddit, openverse, bing, ddg, brave, "
        "flickr, google (comma separated)",
    )
    s.add_argument(
        "--per-query",
        type=int,
        default=100,
        help="keep this low - engines return unrelated filler once "
        "real matches are exhausted, usually past ~100",
    )
    s.add_argument("--min-side", type=int, default=200)
    s.add_argument("--threads", type=int, default=8)
    s.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="raise this on slow or filtered networks",
    )
    s.add_argument(
        "--no-resume",
        action="store_true",
        help="re-run queries already marked complete (costs API "
        "credits again on metered engines)",
    )
    s.add_argument("--google-key", default=os.environ.get("GOOGLE_API_KEY", ""))
    s.add_argument("--google-cx", default=os.environ.get("GOOGLE_CX", ""))
    s.add_argument("--brave-key", default=os.environ.get("BRAVE_API_KEY", ""))
    s.add_argument("--serpapi-key", default=os.environ.get("SERPAPI_KEY", ""))
    s.add_argument("--flickr-key", default=os.environ.get("FLICKR_API_KEY", ""))
    s.set_defaults(func=cmd_scrape)

    fl = sub.add_parser("filter", help="CLIP relevance filter")
    fl.add_argument("--src", default="raw/search")
    fl.add_argument("--out", default="raw/filtered")
    fl.add_argument("--model", default="openai/clip-vit-base-patch32")
    fl.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="0.3 keeps more junk, 0.7 is aggressive",
    )
    fl.add_argument("--batch", type=int, default=32)
    fl.set_defaults(func=cmd_filter)

    f = sub.add_parser("frames", help="YouTube -> frames")
    f.add_argument("--urls", help="text file, one video/playlist URL per line")
    f.add_argument("--search-n", type=int, default=15, help="videos per auto search")
    f.add_argument("--out", default="raw/frames")
    f.add_argument("--workdir", default="_videos")
    f.add_argument("--fps", type=float, default=1.0)
    f.add_argument("--max-side", type=int, default=1280)
    f.add_argument("--keep-video", action="store_true")
    f.add_argument(
        "--js-runtime",
        default="node",
        help="JS runtime for yt-dlp: node, deno, bun, or "
        "'node:C:/path/to/node.exe'. Empty string to omit.",
    )
    f.add_argument(
        "--ffmpeg-location",
        default=".",
        help="folder containing ffmpeg (needed for HLS streams). "
        "Empty string to rely on PATH.",
    )
    f.set_defaults(func=cmd_frames)

    d = sub.add_parser("dedupe", help="perceptual dedupe + size filter")
    d.add_argument("--src", default="raw")
    d.add_argument("--out", default="dataset")
    d.add_argument("--min-side", type=int, default=224)
    d.add_argument("--hash-size", type=int, default=8)
    d.add_argument(
        "--threshold",
        type=int,
        default=5,
        help="max hamming distance to count as duplicate (0=exact)",
    )
    d.set_defaults(func=cmd_dedupe)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
