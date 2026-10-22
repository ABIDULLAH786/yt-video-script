#!/usr/bin/env python3
"""
model_frames.py - YouTube -> frames, per FAMILY and per MODEL, drone-gated.

Why this exists alongside drone_families_dataset.py: that script's volume path
is image search (bing/yandex), which returns the same catalogue photo over and
over. On the 5,822 images it collected, 449 were byte-identical duplicates and
the near-duplicate rate on top of that is higher still - product shots of one
model are all the same three press images. Video frames do not have that
problem: every frame is a genuinely different instant, angle and background.

So this collects from YouTube only, and it works at MODEL granularity rather
than family granularity, because "f1_folding_quad" is 22 different aircraft and
we want breadth across all of them rather than 500 frames of whichever one
YouTube happens to rank first.

  models   : list what will be collected, per family (no network)
  fetch    : search YouTube per model, download, cut frames     [resumable]
  gate     : keep ONLY frames a drone detector actually fires on [the filter]
  report   : per-model counts, so gaps stay visible

RESUMABILITY is per model, not per family: each model gets its own directory
and its own .done marker, so a run stopped halfway is resumed by re-issuing the
same command. Nothing already collected is re-downloaded.

THE TWO KINDS OF USELESS VIDEO, and how each is dealt with:

  1. "talking about the drone" - a review, a person at a desk, spec slides. The
     drone may never appear, or appear for three seconds.
  2. "recorded FROM the drone" - aerial landscape footage. The camera IS the
     drone, so the drone is never in frame. This is the bigger trap because the
     obvious search terms ("4K footage", "cinematic") return almost nothing else.

Search terms are chosen to bias AWAY from both (see MODEL_TEMPLATES - "flight
test" and "takeoff and landing" are filmed from the ground; "footage" and
"cinematic" are filmed from the air). Whatever still gets through is removed by
`gate`, which asks one question of every frame - "is an aircraft visible here?"
- and drops the frames where the answer is no. That single test rejects both
cases at once, because neither has a drone in the picture.

`gate` needs TWO models to answer that question and the reason is measured, not
theoretical: gating on our own detector alone threw away 81% of what it
rejected (see cmd_gate's docstring). It keeps a frame when EITHER the detector
or CLIP sees a drone, and drops only what both reject.

Run from this directory. Needs: yt-dlp, ffmpeg, ultralytics (for `gate`).
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from video_registry import Registry, DEFAULT_PATH as REGISTRY_PATH

CHECKLIST = Path(__file__).parent / "drone-model-checklist-by-family.md"

# Family keys match drone_families_dataset.FAMILIES so the two scripts agree.
FAMILY_KEYS = {
    "1": "f1_folding_quad",
    "2": "f2_micro_selfie",
    "3": "f3_ducted_fpv",
    "4": "f4_open_frame_fpv",
    "5": "f5_enterprise_quad",
    "6": "f6_heavy_lift",
    "7": "f7_fixed_wing_vtol",
}

# One search per template per model. Deliberately ground-filmed phrasings:
# a third-person view of the aircraft is the only kind of frame worth having.
#
# DO NOT add "footage", "cinematic", "4K", "POV" or "FPV view" here. Those are
# the words people put on video shot FROM the aircraft, where it is never in
# frame - the single biggest source of waste in this whole pipeline.
MODEL_TEMPLATES = [
    "{model} flight test",
    "{model} takeoff and landing",
    "{model} hands on outdoor",
]

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".m4v", ".ts", ".mov"}


# ----------------------------------------------------------------- models
def load_models(checklist: Path = CHECKLIST) -> dict:
    """Parse the checklist markdown into {family_key: [model, ...]}.

    Parsed rather than duplicated into a constant: the checklist is the
    document a human maintains, and a second copy in code would drift from it
    the first time someone adds a model.
    """
    if not checklist.exists():
        sys.exit(f"checklist not found: {checklist}")
    txt = checklist.read_text(encoding="utf-8")
    parts = re.split(r"^## (\d)\. (.+)$", txt, flags=re.M)
    out = {}
    for i in range(1, len(parts), 3):
        num, _title, body = parts[i], parts[i + 1], parts[i + 2]
        key = FAMILY_KEYS.get(num)
        if not key:
            continue
        rows = re.findall(r"^\|\s*\d+\s*\|\s*([^|]+?)\s*\|", body, flags=re.M)
        models = [r.strip() for r in rows if r.strip()]
        if models:
            out[key] = models
    return out


def slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()[:60]


def search_terms(model: str) -> list:
    """Search phrasings for one model.

    A model listed as "DJI Mavic 3 / 3 Classic" is really two names; only the
    first is searched, because the slash form returns nothing useful as a
    literal query.
    """
    primary = model.split("/")[0].strip()
    return [t.format(model=primary) for t in MODEL_TEMPLATES]


def resolve_families(spec: str, models: dict) -> list:
    if not spec or spec == "all":
        return list(models)
    out = []
    for token in (t.strip() for t in spec.split(",")):
        if not token:
            continue
        if token in models:
            out.append(token)
            continue
        hit = [k for k in models if k.startswith(f"f{token.lstrip('f')}_")]
        if hit:
            out.append(hit[0])
        else:
            print(f"! unknown family: {token}", file=sys.stderr)
    return out


def cmd_models(args):
    models = load_models()
    fams = resolve_families(args.families, models)
    total = 0
    for fam in fams:
        print(f"\n=== {fam} — {len(models[fam])} models ===")
        for i, m in enumerate(models[fam], 1):
            total += 1
            print(f"  {i:>3}. {m}")
            if args.show_terms:
                for t in search_terms(m):
                    print(f"       ytsearch: {t}")
    print(f"\n{total} models across {len(fams)} families")
    print(f"{total * len(MODEL_TEMPLATES)} YouTube searches at "
          f"{len(MODEL_TEMPLATES)} terms/model")


# ------------------------------------------------------------------ fetch
def _model_dir(out: Path, fam: str, model: str, variant: str = "") -> Path:
    return out / f"{fam}{variant}" / slug(model)


_FRAME_RE = re.compile(r"^.*_t\d+_(.+)_\d{5}\.(?:jpg|jpeg|png)$", re.I)


def build_video_index(out: Path) -> dict:
    """{video_id: (family_dir, model_slug, frame_count)} across EVERY family.

    Scoping the already-have check to one family was not enough. YouTube
    returns the same video for searches of quite different aircraft — measured
    2026-09-21 across the collected set, 133 videos had landed in more than one
    model directory and 38 of those spanned families, one of them in nine
    directories at once. Each copy is a full second set of frames of the same
    footage, and worse, it is usually the WRONG aircraft for at least one of
    the models that took it: "DJI FPV MAX SPEED 140 km/h vs TRAIN" was pulled
    into three heavy-lift agricultural models.

    Built from filenames rather than videos.json so it also covers directories
    collected before manifests existed, and so a hand-filtered directory still
    counts as "already processed".
    """
    idx = {}
    if not out.exists():
        return idx
    for fam_dir in sorted(out.iterdir()):
        if not fam_dir.is_dir():
            continue
        for mdir in sorted(fam_dir.iterdir()):
            if not mdir.is_dir():
                continue
            counts = {}
            for f in mdir.iterdir():
                m = _FRAME_RE.match(f.name)
                if m:
                    counts[m.group(1)] = counts.get(m.group(1), 0) + 1
            for vid, n in counts.items():
                # First writer wins, so the report names where it landed first.
                idx.setdefault(vid, (fam_dir.name, mdir.name, n))
    return idx


def _probe_candidates(term, want, oversample, min_dur, max_dur, js_runtime,
                      cookies_from=None, remote_components=None,
                      sleep_requests=0.0, timeout=None):
    """Ask YouTube for more results than needed, return the first `want` that
    pass the duration filter.

    WHY THIS EXISTS. `ytsearch3:` asks for the top 3 results and `--match-filter`
    then DISCARDS whichever of those 3 are Shorts or hour-long compilations — it
    does not search deeper to replace them. So the filter eats into the quota
    instead of being satisfied from it. Measured 2026-09-17 on a 20-model run:
    60 videos requested, 20 downloaded, five models got nothing at all.

    Probing a wider net first and keeping the first `want` that actually pass
    makes --videos-per-term mean what it says. On the term that had yielded
    zero ("HoverAir X1 flight test"): ytsearch1 -> 0 pass, ytsearch5 -> 3,
    ytsearch15 -> 10. The videos were always there.
    """
    cmd = ["yt-dlp"]
    if js_runtime:
        cmd += ["--js-runtimes", js_runtime]
    if cookies_from:
        cmd += ["--cookies-from-browser", cookies_from]
    if remote_components:
        cmd += ["--remote-components", remote_components]
    if sleep_requests:
        cmd += ["--sleep-requests", str(sleep_requests)]
    cmd += [
        "--simulate", "--no-warnings", "--ignore-errors",
        "--match-filter", f"duration > {min_dur} & duration < {max_dur}",
        "--print", "%(id)s\t%(duration)s\t%(title)s",
        f"ytsearch{want * oversample}:{term}",
    ]
    if timeout is None:
        # Deliberately generous, and the asymmetry is the reason: an over-long
        # timeout costs seconds of waiting, a short one loses the entire search
        # and with it every video that model would have got. Measured 2026-09-19
        # on f4 — the same probe that finishes in 120s on an idle link blew past
        # 210s while downloads were running, and "Flywoo Explorer LR" ended the
        # run with zero frames because all three of its probes were killed.
        timeout = 180 + (want * oversample) * (sleep_requests + 16.0)
    # One retry. Probe failures are usually contention, not a dead search, and
    # retrying one probe is far cheaper than losing a model's whole term.
    last_err = None
    for attempt in (1, 2):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            break
        except subprocess.TimeoutExpired:
            last_err = f"probe timed out after {timeout:.0f}s"
            if attempt == 1:
                print(f"        probe timed out, retrying once…", flush=True)
                time.sleep(5)
    else:
        return [], last_err
    # Distinguish "the search worked, nothing matched the duration filter" from
    # "every request was refused". --ignore-errors makes both look like an empty
    # stdout, and reporting a hard block as "no videos passed the filter" sent a
    # real run chasing a filter problem that did not exist.
    if "not a bot" in r.stderr or "Sign in to confirm" in r.stderr:
        return [], "RATE_LIMITED"
    rows = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0].strip():
            try:
                rows.append((parts[0].strip(), int(float(parts[1])), parts[2]))
            except ValueError:
                continue
    # ALL of them, not rows[:want]. The caller skips candidates whose frames it
    # already has and needs deeper ones to replace them - truncating here meant
    # a term that returned 3 candidates, 1 of them already downloaded, yielded
    # 2 videos instead of 3.
    return rows, None


def cmd_fetch(args):
    """Per-model YouTube search -> download -> frames. Resumable per model."""
    models = load_models()
    fams = resolve_families(args.families, models)
    out = Path(args.out)
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    if args.top_up:
        # Top-up mode. The per-VIDEO check further down is untouched, which is
        # the whole trick: every model is revisited, but each one only downloads
        # videos whose frames are not already on disk. Deleting .done by hand
        # does the same thing; this makes it one flag and says so in the log.
        args.videos_per_term = args.top_up

    # TWO sources for "have I seen this video?", and both are needed.
    #
    # The registry is the durable one: a JSON file that outlives the frames.
    # Triage moves, filters and deletes the raw folders, so a disk scan forgets
    # everything the moment the next pipeline step runs — five of our seven
    # families had their raw folders cleared, and without the registry a re-run
    # would re-download every video in them.
    #
    # The disk scan still runs because it picks up frames that predate the
    # registry, or that someone dropped in by hand. Anything it finds is folded
    # into the registry on the spot, so the scan only ever has to find a thing
    # once.
    registry = Registry(args.registry)
    print("indexing already-collected videos…", flush=True)
    video_index = build_video_index(out)
    adopted = 0
    for vid, (seen_fam, seen_model, seen_n) in video_index.items():
        if not registry.get(vid):
            registry.record(vid, seen_fam, seen_model, frames=seen_n,
                            source="disk-scan")
            adopted += 1
    print(f"  registry: {registry.data['counts'].get('processed', 0):,} videos "
          f"across {registry.data['counts'].get('categories', 0)} categories"
          + (f"  (+{adopted:,} adopted from disk)" if adopted else ""))
    print(f"  on disk : {len(video_index):,} videos\n", flush=True)

    planned = [(f, m) for f in fams for m in models[f]]
    if args.max_per_family:
        # A per-CATEGORY budget, which --top-up cannot express: that one is per
        # model per term, so "2" across 22 models and 3 terms is up to 132
        # videos, not 2.
        #
        # Thinnest model first. With a small budget the order decides who gets
        # it, and spending it on the models with the fewest frames fills the
        # gaps rather than topping up whichever aircraft happens to be listed
        # first.
        #
        # Counted from the REGISTRY, not from the disk. The frames are deleted
        # as each category is triaged — on this corpus only two of eight
        # categories still have a raw folder — so an on-disk count reads zero
        # for almost everything and the sort does nothing at all.
        have = collections.Counter()
        for e in registry.data["videos"].values():
            if e.get("status") == "processed":
                have[(e.get("category") or "", e.get("model") or "")] += \
                    int(e.get("frames") or 0)
        def collected(fam_model):
            f, m = fam_model
            key = (f"{f}{args.variant}", slug(m))
            return have.get(key, 0) or _count_images(_model_dir(out, f, m, args.variant))
        ordered = []
        for f in fams:
            ordered += sorted(((f, m) for m in models[f]), key=collected)
        planned = ordered
        print("thinnest models first (registry frame counts):")
        for f in fams:
            first = [m for ff, m in ordered if ff == f][:3]
            print(f"  {f}: {', '.join(f'{m} ({collected((f, m)):,})' for m in first)}")
        print()
    mode = (f"TOP-UP: +{args.top_up} new video(s) per term for every model "
            f"(already-downloaded videos are skipped)"
            if args.top_up else
            f"{args.videos_per_term} videos per term")
    print(f"{len(planned)} models to collect "
          f"({len(planned) * len(MODEL_TEMPLATES)} searches) — {mode}\n")

    done_n = skipped = 0
    fam_new: dict[str, int] = {}
    for idx, (fam, model) in enumerate(planned, 1):
        if args.max_per_family and fam_new.get(fam, 0) >= args.max_per_family:
            continue
        mdir = _model_dir(out, fam, model, args.variant)
        marker = mdir / ".done"
        if marker.exists() and not args.no_resume and not args.top_up \
                and not args.max_per_family:
            skipped += 1
            print(f"[{idx}/{len(planned)}] {fam}/{model} — done, skipping")
            continue

        mdir.mkdir(parents=True, exist_ok=True)
        print(f"[{idx}/{len(planned)}] {fam} / {model}", flush=True)
        frames_before = _count_images(mdir)
        # Human-readable record of which videos this model was built from.
        # The frame filenames already carry the video id, but nobody wants to
        # reverse-engineer a video list by eye from 1,500 filenames.
        manifest_path = mdir / "videos.json"
        manifest = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
            except Exception:
                manifest = {}

        any_candidates = False
        for ti, term in enumerate(search_terms(model)):
            if args.max_per_family and fam_new.get(fam, 0) >= args.max_per_family:
                break
            vdir = workdir / fam / slug(model) / f"t{ti}"
            vdir.mkdir(parents=True, exist_ok=True)
            print(f"    [search {ti+1}/{len(search_terms(model))}] {term}",
                  flush=True)

            picks, err = _probe_candidates(
                term, args.videos_per_term, args.oversample,
                args.min_duration, args.max_duration, args.js_runtime,
                cookies_from=args.cookies_from_browser,
                remote_components=args.remote_components,
                sleep_requests=args.sleep_requests,
            )
            if err == "RATE_LIMITED":
                print(f"\n!! YouTube is rate-limiting this IP "
                      f"(\"Sign in to confirm you're not a bot\").\n"
                      f"   The SEARCH itself is refused, so nothing will be found.\n"
                      f"   Options: re-run with --cookies-from-browser chrome,\n"
                      f"            or wait a few hours for the block to decay.\n"
                      f"   Frames already collected are safe and will be skipped.\n",
                      file=sys.stderr)
                raise SystemExit(2)
            if err:
                print(f"        ! {err}", file=sys.stderr)
                continue
            if not picks:
                print(f"        no videos passed the "
                      f"{args.min_duration}-{args.max_duration}s filter "
                      f"(searched top {args.videos_per_term * args.oversample})",
                      flush=True)
                continue
            any_candidates = True
            print(f"        {len(picks)} candidate(s) passed the filter "
                  f"(top {args.videos_per_term * args.oversample}); "
                  f"taking {args.videos_per_term} new", flush=True)

            got_new = 0
            for vid, dur, title in picks:
                if got_new >= args.videos_per_term:
                    break
                # Matched on the VIDEO ID ALONE, deliberately not on the
                # term-scoped stem. YouTube returns the same video for several
                # of a model's search phrasings - measured on the real f6 run,
                # 17 videos came back under two or three terms - and a
                # term-scoped check treats each as new, downloading it again and
                # writing a second full set of frames under a different name.
                # The id is what identifies a video; the term it was found by is
                # just provenance.
                # The global check, and it is deliberately CATEGORY-BLIND.
                # The same clip comes back for searches of quite different
                # aircraft; collecting it twice gives two sets of frames of
                # identical footage, and it is the wrong aircraft for at least
                # one of the categories that took it. The id is what identifies
                # a video — the search term that found it is only provenance.
                skip, why = registry.should_skip(vid, args.retry_failed)
                if skip and not args.no_resume:
                    e = registry.get(vid) or {}
                    print(f"        [{vid}] {why}, skipping", flush=True)
                    manifest.setdefault(vid, {
                        "term": ti, "duration": dur, "title": title,
                        "frames": e.get("frames", 0),
                        "already_in": f"{e.get('category')}/{e.get('model')}"})
                    continue
                cmd = ["yt-dlp"]
                if args.js_runtime:
                    cmd += ["--js-runtimes", args.js_runtime]
                if args.cookies_from_browser:
                    cmd += ["--cookies-from-browser", args.cookies_from_browser]
                if args.remote_components:
                    cmd += ["--remote-components", args.remote_components]
                if args.sleep_requests:
                    cmd += ["--sleep-requests", str(args.sleep_requests)]
                if args.sleep_interval:
                    cmd += ["--sleep-interval", str(args.sleep_interval),
                            "--max-sleep-interval",
                            str(max(args.sleep_interval * 3, args.sleep_interval))]
                cmd += [
                    "-f", "bv*[height<=1080]/b[height<=1080]/bv*/b",
                    "--no-warnings",
                    "-o", str(vdir / "%(id)s.%(ext)s"),
                    f"https://www.youtube.com/watch?v={vid}",
                ]
                t0 = time.time()
                dl = subprocess.run(cmd, capture_output=True, text=True)
                if dl.returncode != 0:
                    err_txt = dl.stderr.strip()
                    if "not a bot" in err_txt or "Sign in to confirm" in err_txt:
                        # YouTube throttles an IP after a few hundred downloads.
                        # Every subsequent request fails the same way, so saying
                        # it once and stopping beats 200 identical error lines
                        # and an hour of pointless retries.
                        print(f"\n!! YouTube is rate-limiting this IP "
                              f"(\"Sign in to confirm you're not a bot\").\n"
                              f"   Nothing below this point will download.\n"
                              f"   Fix: re-run with --cookies-from-browser chrome\n"
                              f"   (or brave / edge), or wait a few hours.\n"
                              f"   Models already collected are marked done and "
                              f"will be skipped.\n", file=sys.stderr)
                        raise SystemExit(2)
                    print(f"        [{vid}] DOWNLOAD FAILED: "
                          f"{err_txt[:120]}", file=sys.stderr)
                    # Recorded, not silently dropped. A video that is private,
                    # removed or geo-blocked fails identically every run, and
                    # without this the next run spends the same minutes on it.
                    # Two strikes and it is skipped; --retry-failed overrides.
                    registry.record_failure(vid, f"{fam}{args.variant}",
                                            err_txt[:200], slug(model))
                    continue

                video = next((p for p in sorted(vdir.iterdir())
                              if p.stem == vid and p.suffix.lower() in VIDEO_EXTS),
                             None)
                if video is None:
                    print(f"        [{vid}] no file after download", file=sys.stderr)
                    registry.record_failure(vid, f"{fam}{args.variant}",
                                            "no file after download", slug(model))
                    continue

                stem = f"{slug(model)}_t{ti}_{video.stem}"
                before = len(list(mdir.glob(f"{stem}_*.jpg")))
                subprocess.run(
                    ["ffmpeg", "-hide_banner", "-loglevel", "error",
                     "-i", str(video),
                     "-vf", f"fps={args.fps},scale='min({args.max_side},iw)':-2",
                     "-q:v", "2",
                     str(mdir / f"{stem}_%05d.jpg")],
                    check=False,
                )
                n_frames = len(list(mdir.glob(f"{stem}_*.jpg"))) - before
                size_mb = video.stat().st_size / 1e6
                mins, secs = divmod(dur, 60)
                print(f"        [{vid}] {mins}m{secs:02d}s {size_mb:5.1f}MB "
                      f"-> {n_frames:>4} frames  ({time.time()-t0:.0f}s)  "
                      f"{title[:48]}", flush=True)
                manifest[vid] = {"term": ti, "duration": dur, "title": title,
                                 "frames": n_frames}
                video_index[vid] = (f"{fam}{args.variant}", slug(model), n_frames)
                # Written NOW, one video at a time, not at the end of the run.
                # Runs end badly far more often than they end cleanly — a
                # rate-limit raises SystemExit two lines up, and Ctrl-C and a
                # full disk are just as common. Saving per video means whatever
                # was downloaded before that point is never downloaded again.
                registry.record(vid, f"{fam}{args.variant}", slug(model),
                                frames=n_frames, title=title,
                                duration_s=dur, term=ti)
                got_new += 1
                fam_new[fam] = fam_new.get(fam, 0) + 1
                if args.max_per_family and fam_new[fam] >= args.max_per_family:
                    print(f"    -> {fam} budget reached "
                          f"({args.max_per_family} new video(s))", flush=True)
                    break

                if not args.keep_video:
                    video.unlink(missing_ok=True)
            time.sleep(args.delay)

        if manifest:
            manifest_path.write_text(json.dumps(manifest, indent=2))
        new = _count_images(mdir) - frames_before
        total_frames = _count_images(mdir)
        # Marked done on FRAMES ON DISK, not on new frames and not on the search
        # succeeding. Three ways to get this wrong, all of them met in testing:
        # marking done when the search returned results leaves permanent holes
        # if every download then failed; marking done only on NEW frames means a
        # re-run that legitimately finds the same videos never settles and
        # retries that model forever; marking nothing means no resume at all.
        # Marked done only when frames exist AND at least one search actually
        # returned candidates. Frames-alone was too loose: during the
        # 2026-09-17 rate-limited run every search for DJI Agras T100 came back
        # empty, but the model already had 1,524 frames from an earlier run, so
        # it was marked complete and silently skipped the top-up it was re-run
        # for. A model whose every search returned nothing has not been
        # collected, whatever is already on disk.
        if total_frames > 0 and any_candidates:
            marker.touch()
            done_n += 1
        elif total_frames > 0 and not any_candidates:
            print(f"    -> NOT marked done: every search came back empty "
                  f"({total_frames} frames on disk from an earlier run)",
                  flush=True)
        print(f"    -> {new} new frames, {total_frames} total from "
              f"{len(manifest)} video(s) "
              f"{'(marked done)' if total_frames else '(NOTHING — will retry next run)'}",
              flush=True)

    if not args.keep_video:
        shutil.rmtree(workdir, ignore_errors=True)
    print(f"\nfetched {done_n} models, skipped {skipped} already done.")
    print(f"raw frames under {out}/ — now run `gate` to drop frames with no drone.")


# ------------------------------------------------------------------- gate
CLIP_KEEP = [
    "a photo of a drone",
    "a person holding a drone",
    "a quadcopter sitting on the ground",
    "a drone flying in the sky",
]
CLIP_DROP = [
    "a person talking to the camera",
    "an aerial view of landscape seen from above",
    "an empty room interior",
    "text or graphics on screen",
    "a close-up of a face",
]


class _Clip:
    """CLIP drone-presence scorer. Second opinion for the gate."""

    def __init__(self, name, device):
        import torch
        from transformers import CLIPModel, CLIPProcessor
        self.torch = torch
        self.dev = device
        self.model = CLIPModel.from_pretrained(name).to(device).eval()
        self.proc = CLIPProcessor.from_pretrained(name)
        self.prompts = CLIP_KEEP + CLIP_DROP
        self.n_keep = len(CLIP_KEEP)

    def scores(self, images):
        inp = self.proc(text=self.prompts, images=images,
                        return_tensors="pt", padding=True).to(self.dev)
        with self.torch.no_grad():
            probs = self.model(**inp).logits_per_image.softmax(dim=1)
        return probs[:, :self.n_keep].sum(1).tolist()


def cmd_gate(args):
    """Drop frames with no drone in them — the talking-head and aerial-POV cases.

    A talking-head review and a shot filmed FROM the drone fail the same test:
    no drone in the picture. So one question — "is an aircraft visible here?" —
    removes both.

    WHY THIS IS A UNION OF TWO MODELS AND NOT JUST THE DETECTOR. The obvious
    implementation gates on our own YOLO detector, and it is wrong. Measured
    2026-09-17 on 2,224 f2_micro_selfie frames: v15 at conf 0.15 rejected 1,141,
    and sampling 150 of those rejects put a drone in **81%** of them. A frame
    of a man holding a palm-sized quadcopter straight at the lens was thrown
    away while a frame with the same drone half-hidden behind his arm was kept.

    That is the circularity trap, and it is not academic: filtering training
    data with the model you intend to improve discards exactly what the model
    cannot yet see. Our close-up recall is a known weak spot — v13 oversamples
    the close-up tail 3x for precisely this reason — so a detector-only gate
    would strip out the close-ups that fix it and quietly entrench the gap.

    So a frame is kept when EITHER the detector fires OR CLIP says a drone is
    present. Only frames both reject are dropped; those really are the
    talking-head and aerial-POV junk this command exists to remove. CLIP is not
    ground truth either — it is a differently-wrong model, which is the whole
    point of pairing them. --method lets you run either alone to compare.
    """
    from ultralytics import YOLO
    from PIL import Image

    src_root, out_root = Path(args.src), Path(args.out)
    models = load_models()
    fams = resolve_families(args.families, models)

    use_det = args.method in ("union", "detector")
    use_clip = args.method in ("union", "clip")

    det = None
    if use_det:
        model_path = Path(args.model)
        if not model_path.exists():
            sys.exit(f"detector not found: {model_path}")
        print(f"loading detector {model_path.name} (conf={args.conf})", flush=True)
        det = YOLO(str(model_path))
    clip = None
    if use_clip:
        print(f"loading CLIP {args.clip_model} (threshold={args.clip_threshold})",
              flush=True)
        clip = _Clip(args.clip_model, "cuda" if args.device != "cpu" else "cpu")

    grand = {"kept": 0, "dropped": 0, "det_only": 0, "clip_only": 0, "both": 0}

    for fam in fams:
        fam_src = src_root / fam
        if not fam_src.exists():
            print(f"[skip] {fam}: nothing at {fam_src}")
            continue
        for mdir in sorted(p for p in fam_src.iterdir()
                           if p.is_dir() and not p.name.endswith("__rejected")):
            files = sorted(p for p in mdir.rglob("*") if p.suffix.lower() in IMG_EXTS)
            if not files:
                continue
            out_dir = out_root / fam / mdir.name
            if (out_dir / ".done").exists() and not args.no_resume:
                # Per-model resume. Without it a run interrupted at hour three
                # of a multi-hour family restarts from the first model.
                print(f"  {fam}/{mdir.name} — done, skipping", flush=True)
                continue
            out_dir.mkdir(parents=True, exist_ok=True)
            rej_dir = out_root / fam / f"{mdir.name}__rejected"
            if args.keep_rejects:
                rej_dir.mkdir(parents=True, exist_ok=True)

            kept = dropped = 0
            for i in range(0, len(files), args.batch):
                chunk = files[i:i + args.batch]

                det_hit = [False] * len(chunk)
                if det is not None:
                    res = det.predict([str(p) for p in chunk], conf=args.conf,
                                      imgsz=args.imgsz, device=args.device,
                                      verbose=False)
                    det_hit = [len(r.boxes) > 0 for r in res]

                clip_hit = [False] * len(chunk)
                if clip is not None:
                    ims, ok = [], []
                    for p in chunk:
                        try:
                            ims.append(Image.open(p).convert("RGB")); ok.append(True)
                        except Exception:
                            ok.append(False)
                    if ims:
                        sc = clip.scores(ims)
                        it = iter(sc)
                        clip_hit = [next(it) >= args.clip_threshold if o else False
                                    for o in ok]

                for path, d, c in zip(chunk, det_hit, clip_hit):
                    if d and c:
                        grand["both"] += 1
                    elif d:
                        grand["det_only"] += 1
                    elif c:
                        grand["clip_only"] += 1
                    if d or c:
                        kept += 1
                        shutil.copy2(path, out_dir / path.name)
                    else:
                        dropped += 1
                        if args.keep_rejects:
                            shutil.copy2(path, rej_dir / path.name)

            (out_dir / ".done").touch()
            total = kept + dropped
            rate = kept / total if total else 0.0
            grand["kept"] += kept
            grand["dropped"] += dropped
            flag = "  <- suspect: mostly POV/talking-head" if rate < args.warn_rate else ""
            print(f"  {fam}/{mdir.name:<34} kept {kept:>5} / {total:<6} "
                  f"({rate*100:>5.1f}%){flag}", flush=True)

    tot = grand["kept"] + grand["dropped"]
    if not tot:
        print("\nnothing scored")
        return
    print(f"\nTOTAL kept {grand['kept']} / {tot} ({grand['kept']/tot*100:.1f}%)")
    if args.method == "union":
        print(f"  both agreed      : {grand['both']}")
        print(f"  detector only    : {grand['det_only']}   "
              f"(CLIP missed these)")
        print(f"  CLIP only        : {grand['clip_only']}   "
              f"(the detector missed these — mostly close-ups)")
    print(f"gated set: {out_root}/  (run dedupe next)")
    if args.keep_rejects:
        print("rejects kept alongside — sample them before trusting the cut.")


# ------------------------------------------------------------------- mine
def cmd_mine(args):
    """Hard-negative mining: keep only the no-drone frames the model gets WRONG.

    A negative the detector already handles teaches it nothing — it is a
    correct answer being repeated. The informative negatives are the ones it
    false-fires on, and those are the only ones worth the disk and the training
    time. So this runs the CURRENT model over frames a human has already judged
    to contain no drone, and keeps the frames where it fires anyway.

    Candidates are the raw frames MINUS the ones hand-filtering kept, i.e.
    exactly what a person looked at and rejected. That matters: mining against
    unverified frames would harvest real drones the model spotted and label
    them negative, which is worse than collecting nothing.

    Run at the PRODUCTION confidence, not a low one. The question being asked
    is "would this fire an alarm on site", and a detection at 0.2 that never
    reaches the 0.45 threshold is not a false alarm anyone would see.

    Per model, resumable, one at a time — a family is 20-odd models and the
    point is to look at each one's yield before moving on.
    """
    from ultralytics import YOLO

    models = load_models()
    fams = resolve_families(args.families, models)
    raw_root, out_root = Path(args.raw), Path(args.out)
    kept_root = Path(args.kept) if args.kept else None

    mpath = Path(args.model)
    if not mpath.exists():
        sys.exit(f"model not found: {mpath}")
    only = {slug(o) for o in args.only.split(",")} if args.only else None

    print(f"model {mpath.name} at conf={args.conf}\n", flush=True)
    det = YOLO(str(mpath))

    grand_c = grand_k = 0
    for fam in fams:
        for model in models[fam]:
            ms = slug(model)
            if only and ms not in only:
                continue
            src = raw_root / fam / ms
            if not src.is_dir():
                continue
            out_dir = out_root / fam / ms
            if (out_dir / ".done").exists() and not args.no_resume:
                print(f"  {fam}/{ms} — done, skipping")
                continue

            kept_names = set()
            if kept_root:
                kd = kept_root / ms
                if kd.is_dir():
                    kept_names = {p.name for p in kd.glob("*.jpg")}
            cands = sorted(p for p in src.glob("*.jpg") if p.name not in kept_names)
            if not cands:
                print(f"  {fam}/{ms} — no candidates")
                continue

            out_dir.mkdir(parents=True, exist_ok=True)
            rows, hits = [], 0
            for i in range(0, len(cands), args.batch):
                chunk = cands[i:i + args.batch]
                res = det.predict([str(p) for p in chunk], conf=args.conf,
                                  imgsz=args.imgsz, device=args.device, verbose=False)
                for path, r in zip(chunk, res):
                    if len(r.boxes) == 0:
                        continue
                    hits += 1
                    conf = float(max(b.conf.item() for b in r.boxes))
                    shutil.copy2(path, out_dir / path.name)
                    rows.append((conf, len(r.boxes), path.name))

            rows.sort(reverse=True)
            with open(out_dir / "hard_negatives.csv", "w", encoding="utf-8") as fh:
                fh.write("max_conf,boxes,filename\n")
                for c, n, name in rows:
                    fh.write(f"{c:.4f},{n},{name}\n")
            (out_dir / ".done").touch()

            grand_c += len(cands); grand_k += hits
            rate = hits / len(cands) * 100
            worst = f"  worst {rows[0][0]:.2f}" if rows else ""
            print(f"  {fam}/{ms:<34} {len(cands):>6} candidates -> "
                  f"{hits:>5} false fires ({rate:>5.2f}%){worst}", flush=True)

    if grand_c:
        print(f"\nTOTAL {grand_k:,} hard negatives from {grand_c:,} candidates "
              f"({grand_k/grand_c*100:.2f}%)")
        print(f"under {out_root}/ — label these as negatives (no boxes)")


# ----------------------------------------------------------------- report
def _count_images(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for p in path.rglob("*") if p.suffix.lower() in IMG_EXTS)


def cmd_report(args):
    models = load_models()
    fams = resolve_families(args.families, models)
    raw_root, gated_root = Path(args.raw), Path(args.gated)

    grand_raw = grand_gated = 0
    missing = []
    for fam in fams:
        print(f"\n=== {fam} ===")
        for model in models[fam]:
            mdir = _model_dir(raw_root, fam, model)
            raw_n = _count_images(mdir)
            gated_n = _count_images(gated_root / fam / slug(model))
            grand_raw += raw_n
            grand_gated += gated_n
            done = (mdir / ".done").exists()
            if raw_n == 0:
                missing.append(f"{fam}/{model}")
            keep = f"{gated_n/raw_n*100:5.1f}%" if raw_n else "    -"
            print(f"  {'OK ' if done else '   '} {model[:40]:<40} "
                  f"raw {raw_n:>6}  gated {gated_n:>6}  keep {keep}")
    print(f"\nTOTAL raw {grand_raw}  gated {grand_gated}")
    if missing:
        print(f"\n{len(missing)} models with NO frames yet:")
        for m in missing[:30]:
            print(f"  {m}")
        if len(missing) > 30:
            print(f"  ... and {len(missing)-30} more")


# -------------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(
        description="Per-model YouTube frame collection, drone-gated",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="families: all | f1..f7 | comma separated keys",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("models", help="list models per family (no network)")
    m.add_argument("--families", default="all")
    m.add_argument("--show-terms", action="store_true")
    m.set_defaults(func=cmd_models)

    f = sub.add_parser("fetch", help="YouTube -> frames, per model, resumable")
    f.add_argument("--families", default="all")
    f.add_argument("--out", default="raw/models")
    f.add_argument("--workdir", default="_videos")
    f.add_argument("--videos-per-term", type=int, default=3,
                   help="videos to actually KEEP per search term")
    f.add_argument("--oversample", type=int, default=5,
                   help="search this many times --videos-per-term, then keep "
                        "the first that pass the duration filter. Without it "
                        "the filter eats the quota — see _probe_candidates()")
    f.add_argument("--fps", type=float, default=0.5)
    f.add_argument("--max-side", type=int, default=1280)
    f.add_argument("--min-duration", type=int, default=30,
                   help="seconds; skips shorts and clip-farm stubs")
    f.add_argument("--max-duration", type=int, default=900,
                   help="seconds; skips hour-long compilations")
    f.add_argument("--delay", type=float, default=1.0)
    # Throttling. YouTube's block is triggered by request RATE from one IP, so
    # spreading the same work over more time is what avoids it — there is no
    # trick that makes 1,350 downloads look like light use.
    f.add_argument("--sleep-requests", type=float, default=0.0,
                   help="seconds between yt-dlp's own HTTP requests (try 1-2)")
    f.add_argument("--sleep-interval", type=float, default=0.0,
                   help="min seconds between video downloads; the max is 3x "
                        "this and randomised (try 5)")
    f.add_argument("--cookies-from-browser", default=None,
                   help="chrome|brave|edge|firefox — YouTube blocks an IP after "
                        "a few hundred downloads ('Sign in to confirm you are "
                        "not a bot'); browser cookies lift that. Close the "
                        "browser first, it locks its cookie DB.")
    f.add_argument("--remote-components", default="ejs:github",
                   help="yt-dlp JS challenge solver. Required since YouTube's "
                        "2026 n-challenge — without it downloads fail with "
                        "'The page needs to be reloaded'. '' to disable.")
    f.add_argument("--js-runtime", default="node",
                   help="yt-dlp JS runtime: node|deno|bun; '' to omit")
    f.add_argument("--keep-video", action="store_true")
    f.add_argument("--no-resume", action="store_true",
                   help="ignore BOTH .done and the per-video check — redownloads "
                        "everything. Rarely what you want; see --top-up.")
    f.add_argument("--registry", default=str(REGISTRY_PATH),
                   help="the global video record. Every id in it is skipped, "
                        "whichever category asks for it, and every new video is "
                        "written to it as soon as its frames are cut.")
    f.add_argument("--max-per-family", type=int, default=0, metavar="N",
                   help="stop a category once N NEW videos have been "
                        "downloaded for it. A per-category budget, which "
                        "--top-up cannot express (that is per model per term). "
                        "Models are visited thinnest-first so a small budget "
                        "fills the gaps.")
    f.add_argument("--retry-failed", action="store_true",
                   help="also retry videos the registry has marked failed "
                        "(private, removed, geo-blocked). Off by default so a "
                        "dead video is not re-attempted every single run.")
    f.add_argument("--variant", default="", metavar="SUFFIX",
                   help="write into <family><SUFFIX>/ instead of <family>/, e.g. "
                        "--variant _v2 -> raw/models/f6_heavy_lift_v2. Videos "
                        "already collected under ANY variant of that family are "
                        "still skipped, so a v2 run only fetches new material.")
    f.add_argument("--top-up", type=int, default=None, metavar="N",
                   help="add N MORE new videos per search term to every model in "
                        "the family, including ones already marked done. Videos "
                        "already on disk are still skipped, so this only ever "
                        "fetches material you do not have.")
    f.set_defaults(func=cmd_fetch)

    g = sub.add_parser("gate", help="keep only frames a drone detector fires on")
    g.add_argument("--families", default="all")
    g.add_argument("--src", default="raw/models")
    g.add_argument("--out", default="gated/models")
    g.add_argument("--method", default="union",
                   choices=["union", "detector", "clip"],
                   help="union (default, detector OR clip — see the source "
                        "docstring for why detector-alone is wrong)")
    g.add_argument("--model", default="../model-testing/models/v15-best.pt")
    g.add_argument("--conf", type=float, default=0.15,
                   help="LOW on purpose — see the circularity note in the source")
    g.add_argument("--clip-model", default="openai/clip-vit-base-patch32")
    g.add_argument("--clip-threshold", type=float, default=0.5,
                   help="summed probability of the drone-present prompts")
    # 960/4 rather than 1280/16: with `union` both the detector and CLIP sit on
    # the GPU at once, and 1280x16 OOMs a 4GB card (measured on a GTX 1650,
    # 3.63 GiB). Raise both on a bigger card.
    g.add_argument("--imgsz", type=int, default=960)
    g.add_argument("--device", default="0")
    g.add_argument("--batch", type=int, default=4)
    g.add_argument("--no-resume", action="store_true",
                   help="re-score models already marked done")
    g.add_argument("--keep-rejects", action="store_true",
                   help="keep dropped frames for eyeballing — strongly advised")
    g.add_argument("--warn-rate", type=float, default=0.10,
                   help="flag a model whose keep-rate is below this")
    g.set_defaults(func=cmd_gate)

    mi = sub.add_parser("mine", help="keep only no-drone frames the model false-fires on")
    mi.add_argument("--families", default="all")
    mi.add_argument("--only", default=None,
                    help="one model slug, or several comma separated — process "
                         "a single model at a time")
    mi.add_argument("--raw", default="raw/models")
    mi.add_argument("--kept", default=None,
                    help="the hand-filtered directory for this family; its "
                         "filenames are EXCLUDED, so candidates are exactly the "
                         "frames a person rejected")
    mi.add_argument("--out", default="hardneg")
    mi.add_argument("--model", default="../model-testing/models/v16-best.pt")
    mi.add_argument("--conf", type=float, default=0.45,
                    help="production threshold — a detection below it is not an "
                         "alarm anyone would see")
    mi.add_argument("--imgsz", type=int, default=960)
    mi.add_argument("--device", default="0")
    mi.add_argument("--batch", type=int, default=8)
    mi.add_argument("--no-resume", action="store_true")
    mi.set_defaults(func=cmd_mine)

    r = sub.add_parser("report", help="per-model counts and gaps")
    r.add_argument("--families", default="all")
    r.add_argument("--raw", default="raw/models")
    r.add_argument("--gated", default="gated/models")
    r.set_defaults(func=cmd_report)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
