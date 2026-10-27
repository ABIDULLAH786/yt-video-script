#!/usr/bin/env python3
"""
roboflow_upload.py - send the filtered real frames to Roboflow for annotation.

Uploads the CLEAN frames only, from each band's images/ folder. The annotated/
copies are never touched: they have the detector's boxes burned into the pixels,
and a model trained on those learns "red rectangle = drone".

ONE PROJECT, ONE BATCH PER FAMILY. A batch is Roboflow's own grouping, so the
seven families stay visible and assignable inside a single project rather than
becoming seven projects to export, version and wire into the training notebook
separately. Each image is also tagged with its family and its confidence band,
so the Roboflow UI can filter on either.

RESUMABLE. Every successful upload is appended to a local log, and a re-run
skips what is already there. 30,000 images take hours and the connection will
drop at least once; without this a restart would begin from the first frame and
duplicate everything already sent.

    export ROBOFLOW_API_KEY=...

    ./roboflow_upload.py --plan                       # counts and size, no network
    ./roboflow_upload.py --create                     # make the project once
    ./roboflow_upload.py --families f1_folding_quad   # one family first
    ./roboflow_upload.py                              # everything, resumable

Get the key from Roboflow: Settings -> API Keys -> Private API Key.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from frame_meta import video_id

HERE = Path(__file__).resolve().parent
FILTERED = HERE / "filtered" / "AI Filtered"
LOG = HERE / "roboflow_uploaded.json"

WORKSPACE = "abid-ullah"
PROJECT = "drone-families-dataset-from-yt"
BANDS = ["1_fires_high_conf70plus", "2_fires_mid_conf50to70",
         "3_fires_low_conf25to50", "4_silent_no_detection"]
# Short tag per band, so the UI filter reads cleanly.
BAND_TAG = {"1_fires_high_conf70plus": "conf-high",
            "2_fires_mid_conf50to70": "conf-mid",
            "3_fires_low_conf25to50": "conf-low",
            "4_silent_no_detection": "model-missed"}


def collect(families: list[str] | None):
    """[(family, band, path)] for every CLEAN frame to upload.

    images/ only. The annotated/ copies have the detector's boxes burned into
    the pixels; uploading those teaches the model that a red rectangle is a
    drone, which is the one mistake that would poison the whole set.
    """
    out = []
    if not FILTERED.is_dir():
        sys.exit(f"not found: {FILTERED}")
    for fam in sorted(p for p in FILTERED.iterdir() if p.is_dir()):
        if families and fam.name not in families:
            continue
        for band in BANDS:
            d = fam / band / "images"
            if d.is_dir():
                out += [(fam.name, band, p) for p in sorted(d.glob("*.jpg"))]
    return out


def split_for(name: str, valid_pct: float, test_pct: float,
              key: str | None = None) -> str:
    """Which split this frame belongs to.

    Keyed on the VIDEO, not the frame. Frames are cut at 0.5 fps, so
    consecutive ones are two seconds apart and all but identical; splitting
    per frame puts near-twins on both sides and the validation score measures
    memorisation rather than generalisation. Measured on this set: a per-frame
    hash put 514 of 974 videos — 53 percent — into both train and valid.

    Hashed rather than shuffled so the answer is the same on every run. A
    30,000-image upload is interrupted and resumed several times, and a random
    split would reshuffle the held-out set halfway through.
    """
    h = int(hashlib.md5((key or name).encode()).hexdigest()[:8], 16) % 10000
    if h < valid_pct * 100:
        return "valid"
    if h < (valid_pct + test_pct) * 100:
        return "test"
    return "train"


def build_split_map(items, valid_pct: float, test_pct: float) -> dict:
    """{filename: split}, allocated per family and never splitting a video.

    Whole videos go to one side, because frames cut at 0.5 fps are two seconds
    apart and all but identical — a per-frame split put 514 of 974 videos on
    both sides and would have measured memorisation. But videos differ hugely
    in length, so simply hashing the video id overshot badly: f6 landed at
    10.6 percent valid and f4 at 1.7. Allocating whole videos until the
    family's frame target is met keeps both properties at once.

    Videos are ordered by a hash of their id, so the choice is arbitrary but
    identical on every run — a resumed upload must not reshuffle the split.
    """
    import collections
    byfam = collections.defaultdict(lambda: collections.defaultdict(list))
    for fam, _band, p in items:
        byfam[fam][video_id(p) or f"__{p.name}"].append(p.name)

    out = {}
    for fam, vids in byfam.items():
        total = sum(len(v) for v in vids.values())
        order = sorted(vids, key=lambda v: hashlib.md5(v.encode()).hexdigest())
        targets = [("valid", total * valid_pct / 100.0),
                   ("test", total * test_pct / 100.0)]
        i = 0
        for name, target in targets:
            got = 0
            while got < target and i < len(order):
                # Stop before a video that would overshoot the target by more
                # than it would undershoot by skipping it.
                n = len(vids[order[i]])
                if got and abs(got + n - target) > abs(got - target):
                    break
                for f in vids[order[i]]:
                    out[f] = name
                got += n
                i += 1
        for v in order[i:]:
            for f in vids[v]:
                out[f] = "train"
    return out


def load_log() -> set:
    if not LOG.exists():
        return set()
    try:
        return set(json.loads(LOG.read_text())["uploaded"])
    except Exception:
        return set()


def save_log(done: set) -> None:
    tmp = LOG.with_suffix(".tmp")
    tmp.write_text(json.dumps({"uploaded": sorted(done)}, indent=0))
    tmp.replace(LOG)


def cmd_plan(items, done, valid_pct, test_pct, forced, smap):
    import collections
    per, size = collections.Counter(), collections.Counter()
    sp = collections.defaultdict(collections.Counter)
    for fam, band, p in items:
        if p.name in done:
            continue
        per[fam] += 1
        size[fam] += p.stat().st_size
        sp[fam][forced or smap.get(p.name, 'train')] += 1
    print(f"{'family':<30}{'images':>9}{'size':>9}{'train':>8}{'valid':>7}{'test':>7}")
    print("-" * 70)
    tot = collections.Counter()
    for f in sorted(per):
        tot.update(sp[f])
        print(f"{f:<30}{per[f]:>9,}{size[f]/1e9:>8.2f}G"
              f"{sp[f]['train']:>8,}{sp[f]['valid']:>7,}{sp[f]['test']:>7,}")
    print("-" * 70)
    print(f"{'TO UPLOAD':<30}{sum(per.values()):>9,}{sum(size.values())/1e9:>8.2f}G"
          f"{tot['train']:>8,}{tot['valid']:>7,}{tot['test']:>7,}")
    if done:
        print(f"\nalready uploaded, will be skipped: {len(done):,}")
    n = sum(per.values())
    print(f"\nat ~2 images/sec that is roughly {n/2/3600:.1f} h; "
          f"at ~5/sec, {n/5/3600:.1f} h.")
    print(f"project: {WORKSPACE}/{PROJECT}, one batch per family")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--families", default="",
                    help="comma-separated family folder names; default is all")
    ap.add_argument("--project", default=PROJECT)
    ap.add_argument("--workspace", default=WORKSPACE)
    ap.add_argument("--plan", action="store_true",
                    help="counts and size only, no network")
    ap.add_argument("--create", action="store_true",
                    help="create the project, then exit")
    ap.add_argument("--split", default="", choices=["", "train", "valid", "test"],
                    help="force every image into one split; default is to "
                         "divide each family by --valid-pct / --test-pct")
    ap.add_argument("--with-labels", action="store_true",
                    help="attach <band>/labels/<stem>.txt where export_labels.py "
                         "wrote one, so the frame arrives already boxed. Frames "
                         "with no label file are uploaded bare and queued for "
                         "annotation.")
    ap.add_argument("--valid-pct", type=float, default=4.0)
    ap.add_argument("--test-pct", type=float, default=0.0,
                    help="held out per family, in percent")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N uploads, for a trial run")
    ap.add_argument("--api-key", default=os.environ.get("ROBOFLOW_API_KEY", ""))
    args = ap.parse_args()

    fams = [f.strip() for f in args.families.split(",") if f.strip()] or None
    items = collect(fams)
    if not items:
        sys.exit("no images found — check --families against the folder names")
    done = load_log()
    # Computed from EVERY frame of the chosen families, not just the ones left
    # to send, so a resumed run assigns the same splits as the first one.
    smap = build_split_map(items, args.valid_pct, args.test_pct)

    if args.plan:
        cmd_plan(items, done, args.valid_pct, args.test_pct, args.split, smap)
        return

    if not args.api_key:
        sys.exit("no API key. Either:\n"
                 "    export ROBOFLOW_API_KEY=...\n"
                 "or pass --api-key. Roboflow: Settings -> API Keys -> Private.")

    from roboflow import Roboflow
    rf = Roboflow(api_key=args.api_key)
    ws = rf.workspace(args.workspace)

    if args.create:
        # Single class on purpose: the detector is drone / no-drone, and the
        # non-drone frames are kept as Marked Null negatives rather than as
        # their own class.
        ws.create_project(project_name=args.project,
                          project_type="object-detection",
                          project_license="Private",
                          annotation="drone")
        print(f"created {args.workspace}/{args.project}")
        print("now run without --create to upload")
        return

    project = ws.project(args.project)
    todo = [it for it in items if it[2].name not in done]
    print(f"{len(items):,} frames found, {len(done):,} already uploaded, "
          f"{len(todo):,} to send\n")

    ok = fail = 0
    t0 = time.time()
    try:
        for i, (fam, band, path) in enumerate(todo, 1):
            if args.limit and ok >= args.limit:
                break
            try:
                split = args.split or smap.get(path.name, "train")
                # Send the label alongside when export_labels.py made one.
                # A frame with no file is uploaded bare on purpose: it goes
                # into Roboflow's annotation queue, where an empty label
                # would instead mark it "no objects here" and hide it.
                lab = path.parent.parent / "labels" / f"{path.stem}.txt"
                ann = str(lab) if (args.with_labels and lab.exists()) else None
                project.upload(str(path), annotation_path=ann,
                               split=split, batch_name=fam,
                               tag_names=[fam, BAND_TAG.get(band, band)],
                               num_retry_uploads=3)
                done.add(path.name)
                ok += 1
            except Exception as e:
                fail += 1
                print(f"  ! {path.name}: {str(e)[:100]}", file=sys.stderr)
            if ok and ok % 100 == 0:
                save_log(done)
                rate = ok / max(time.time() - t0, 1)
                left = (len(todo) - i) / max(rate, 0.01) / 3600
                print(f"  {ok:,}/{len(todo):,}  {rate:.1f}/s  "
                      f"~{left:.1f} h left  ({fail} failed)", flush=True)
    except KeyboardInterrupt:
        print("\ninterrupted — progress saved, re-run the same command to continue")
    finally:
        save_log(done)

    print(f"\nuploaded {ok:,}, failed {fail}, logged {len(done):,} in total")
    print(f"log: {LOG}")
    if fail:
        print("re-run the same command; only the failures will be retried")


if __name__ == "__main__":
    main()
