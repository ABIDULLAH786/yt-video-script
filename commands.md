# Dataset collection — commands

YouTube-only, per family and per model, resumable, drone-gated.

Image search (bing/yandex) is **no longer used** — see [Why image search was
dropped](#why-image-search-was-dropped) at the bottom. Everything below runs
from this directory (`data-fetching-scripts/`) with the project venv:

```bash
cd data-fetching-scripts
source ../.venv/bin/activate
```

---

## The commands in use now (October 2026)

**Never pass `--cookies-from-browser` to `fetch`.** YouTube runs a SABR-only
streaming experiment on signed-in accounts: with cookies attached the adaptive
formats arrive with no URL and are dropped, leaving 640x360 as the only format
offered. Measured on four videos — 640x360 with cookies, 1920x1080 without.
Downloads now refuse anything under 1280x720, so with cookies every download
fails with "Requested format is not available". Use the pacing flags against
rate-limiting instead.

### One new video for every aircraft in a category

```bash
./model_frames.py fetch --families 1 --sweep oct-round1 \
    --max-per-family 30 --max-per-model 1 \
    --min-duration 180 --sleep-requests 2 --sleep-interval 5
```

Re-issue the identical line after a Ctrl-C and it continues from the model it
stopped at. To go round again from the first model, add `--restart-sweep`.

### Three videos from one aircraft, the thinnest first

```bash
./model_frames.py fetch --families 1 --max-per-family 3 \
    --min-duration 180 --sleep-requests 2 --sleep-interval 5
```

### Budgets — these are three different things

| flag | means |
|---|---|
| `--max-per-family N` | stop the CATEGORY after N new videos. With nothing else set, all N come from the first model that has them |
| `--max-per-model N` | stop a MODEL after N, then move to the next one |
| `--top-up N` | N new videos per model **per search term**, so 3 terms x 22 models x N |

`--max-per-family 3` alone gave 3 videos from one model and left the other 21
untouched. Pair it with `--max-per-model 1` to spread the same budget across
three aircraft, or set the family budget above the model count to sweep them
all.

### `--sweep` — the durable resume

A sweep is one pass over a category's models. Each finished model is recorded
**in `video_registry.json`**, not as a `.done` file in the model folder, because
those folders are deleted once their frames move into the filtered set — which
is exactly when a `.done` marker would disappear.

Continue an interrupted sweep — the identical line, nothing added:

```bash
./model_frames.py fetch --families 1 --sweep oct-round1 \
    --max-per-family 30 --max-per-model 1 \
    --min-duration 180 --sleep-requests 2 --sleep-interval 5
```

Start the sweep again from the first model:

```bash
./model_frames.py fetch --families 1 --sweep oct-round1 --restart-sweep \
    --max-per-family 30 --max-per-model 1 \
    --min-duration 180 --sleep-requests 2 --sleep-interval 5
```

`--restart-sweep` re-downloads nothing: it only forgets which *models* were
visited. Every video already collected stays in the registry and is still
skipped, so a second round naturally finds different videos. One sweep name can
cover several categories at once (`--families 1,2,3,4,5,6`).

### Resolution

`--min-width 1280 --min-height 720` are the defaults. The format selector asks
only for streams at or above them with no low fallback, and the downloaded file
is then measured with ffprobe before any frames are cut — the selector is a
request, the file is the fact. Frames are never upscaled, so `--max-side 1280`
cannot rescue a 640-wide source.

### The registry

`video_registry.json` holds every video ever processed. Before any download
`fetch` asks whether that id has been seen **by any category**, and skips it if
so. Commit and push it after a collection run; it is shared state.

```bash
./video_registry.py stats                       # counts per category
./video_registry.py check <url-or-id> [...]     # would this be skipped?
./video_registry.py list --category f1_folding_quad
./video_registry.py remove <url-or-id>          # collect it again
```

---

## The pipeline

```
models  ->  fetch  ->  gate  ->  dedupe  ->  report
(plan)     (YouTube)  (drop     (drop      (what's
                       no-drone  near-dups) still thin)
                       frames)
```

**Everything is resumable per model.** Each model gets its own directory and
its own `.done` marker. Stop a run at any point — Ctrl-C, closed laptop, dead
network — and re-issue the *same command*; it skips what is finished and picks
up where it left off. Nothing already downloaded is fetched twice.

---

## 1. `models` — see the plan before spending bandwidth

No network. Lists what would be collected.

```bash
# everything: 150 models across 7 families
python model_frames.py models

# one family, and show the actual YouTube searches it will run
python model_frames.py models --families f2_micro_selfie --show-terms

# a few families (f1..f7, or full keys, comma separated)
python model_frames.py models --families f3,f4,f6
```

---

## 2. `fetch` — YouTube → frames, per model

```bash
# one family first — always do this before a full run
python model_frames.py fetch --families f2_micro_selfie

# everything
python model_frames.py fetch --families all

# gentler / smaller trial
python model_frames.py fetch --families f6_heavy_lift \
  --videos-per-term 1 --fps 0.25 --max-duration 400
```

| flag | default | what it does |
|---|---|---|
| `--families` | `all` | `all`, or `f1..f7`, or comma-separated keys |
| `--out` | `raw/models` | `<out>/<family>/<model_slug>/` |
| `--videos-per-term` | `3` | videos actually **kept** per term, so 9 videos/model |
| `--oversample` | `5` | searches 5× that many, keeps the first that pass — see below |
| `--fps` | `0.5` | one frame every 2 s |
| `--min-duration` | `30` | skips Shorts and clip-farm stubs |
| `--max-duration` | `900` | skips hour-long compilations |
| `--js-runtime` | `node` | yt-dlp needs one since 2025.11.12, else YouTube 403s |
| `--keep-video` | off | keep the mp4s (they are deleted after framing) |
| `--top-up N` | off | revisit every model, add N new videos per term (see above) |
| `--cookies-from-browser` | off | **do not use** — caps every download at 640x360, see the SABR note at the top |
| `--min-width` / `--min-height` | `1280` / `720` | refuse a source smaller than this |
| `--sweep NAME` | off | record finished models in the registry so a run resumes |
| `--max-per-model N` | off | stop a model after N new videos |
| `--max-per-family N` | off | stop a category after N new videos |
| `--sleep-requests` | `0` | seconds between yt-dlp HTTP requests (use 2) |
| `--sleep-interval` | `0` | min seconds between downloads, max is 3x (use 8) |
| `--no-resume` | off | ignore `.done` **and** the per-video check — redownloads everything |

### Why `--oversample` exists

`ytsearch3:` asks YouTube for the top 3 results, and `--match-filter` then
**discards** whichever of those 3 are Shorts or hour-long compilations — it does
not search deeper to replace them. The filter ate the quota.

Measured on a 20-model run before the fix: **60 videos requested, 20 downloaded**,
and five models got nothing at all. On one term that had yielded zero:

```
ytsearch1   ->  0 videos pass the 30-900s filter
ytsearch5   ->  3 pass
ytsearch15  -> 10 pass
```

`fetch` now probes `--videos-per-term × --oversample` results first and keeps the
first ones that actually pass, so the flag means what it says. Same run after the
fix: DJI Neo went from **0 frames to 541**.

Raise `--oversample` for rare airframes with thin YouTube coverage; lower it to
save probe time on well-covered ones.

### Reading the log

```
[1/20] f2_micro_selfie / DJI Neo
    [search 1/3] DJI Neo flight test
        2 video(s) selected from top 12
        [XOeV3JyzHXw] 3m41s  66.8MB ->  111 frames  (24s)  Dji NEO - Return To Home Test
```

Per search: how many videos were selected and out of how many probed. Per video:
YouTube id, duration, download size, frames extracted, seconds taken, and title.
A search that selects 0 prints why, naming the duration window it filtered on.

**Scale before you launch `--families all`:** 150 models × 3 terms × 3 videos =
**1,350 videos**. At 0.5 fps over clips up to 15 min that is on the order of
100k+ raw frames and tens of GB. Do one family, look at what lands, then decide.

**A model that yields 0 frames is deliberately NOT marked done** — all three of
its searches fell outside the duration filter, or the network failed. It will be
retried on the next run rather than silently skipped forever.

### The two commands you will actually use

**1. Collect a family (first run, or resume an interrupted one)**

```bash
python model_frames.py fetch --families f7_fixed_wing_vtol \
  --sleep-requests 2 --sleep-interval 8 --delay 5
```

> **Cookies were removed from this line.** `--cookies-from-browser` now caps
> every download at 640x360 — see the SABR note at the top.

Finished models are skipped, unfinished ones are picked up where they stopped.
Re-issue the identical line after a crash, a closed laptop or a Ctrl-C. Once
every model is done this prints `fetched 0 models, skipped 22 already done` and
exits — which is when you want the second command.

**2. Top up a family that is already done — add N more videos per model**

```bash
python model_frames.py fetch --families f7_fixed_wing_vtol --top-up 2 \
  --sleep-requests 2 --sleep-interval 8 --delay 5
```

`--top-up N` revisits **every** model in the family, ignoring `.done`, and adds
N new videos per search term. Videos already in the registry are still skipped,
so it only ever downloads material you do not have:

```
[1/22] f7_fixed_wing_vtol / WingtraOne Gen II
    [search 1/3] WingtraOne Gen II flight test
        5 candidate(s) passed the filter (top 5); taking 1 new
        [lWjpEd5GlUw] already have 220 frames, skipping
        [EEWP4Ht-7zI] already have 195 frames, skipping
        [_D8Ok9_cHiM] 6m05s  96.0MB ->  182 frames  (40s)  WingtraOne GEN II - The Ultimate...
    -> 262 new frames, 1485 total from 10 video(s)
```

Run it repeatedly to keep growing a family. Each pass digs further down the
search results, so yield falls off as the good videos get used up — a term that
prints only `already have ... skipping` lines has been exhausted at the current
`--oversample`; raise that to search deeper.

Do **not** use `--no-resume` for this. It ignores the per-video check as well
and re-downloads everything you already have.

### Resume works at three levels

- **per video** — `video_registry.json` records every id ever processed, and the
  check is category-blind, so a clip already taken under one aircraft is never
  taken again under another. This replaced an on-disk scan, which forgot
  everything as soon as triage deleted the frames: it had lost 939 of the 1,366
  collected videos.
- **per sweep** — `--sweep NAME` records each finished model in the registry, so
  an interrupted pass over a category continues at the model it stopped at.
  Survives the model folder being deleted.
- **per model** — `.done` in the model dir. The oldest of the three, and the
  weakest: it disappears with the folder.

Frame names are deterministic (`<model>_t<term>_<videoid>_<n>.jpg`), so
re-running a model **overwrites its own frames rather than duplicating them**.
Deleting downloaded data to "start clean" is never necessary.

Note YouTube search results are not stable between runs — a re-run usually finds
some videos it saw before (skipped) and some new ones (added). That grows the set
rather than repeating it.

### `videos.json` — what a model was built from

Every model dir gets one, listing each video used:

```json
{
  "GL1DYTHjB18": {
    "term": 0,
    "duration": 310,
    "title": "DJI Neo Drone Flight Test | Hands-On Review & Aerial Footage",
    "frames": 155
  }
}
```

That is the answer to "which videos have I already done" — no need to
reverse-engineer it from 1,500 filenames.

---

## 3. `gate` — drop frames with no drone in them

This is the step that removes the two kinds of useless video: **someone talking
about a drone**, and **footage recorded from the drone** (where the aircraft is
never in frame). Both fail the same test.

```bash
# default and recommended
python model_frames.py gate --families f2_micro_selfie --keep-rejects

# everything, once you trust the cut
python model_frames.py gate --families all --keep-rejects
```

| flag | default | notes |
|---|---|---|
| `--method` | `union` | `union` \| `detector` \| `clip` — **keep `union`**, see below |
| `--model` | `../model-testing/models/v15-best.pt` | our detector |
| `--conf` | `0.15` | deliberately far below the 0.45 used in production |
| `--clip-threshold` | `0.5` | summed probability of the drone-present prompts |
| `--imgsz` / `--batch` | `960` / `4` | sized for a 4 GB card; raise on a bigger GPU |
| `--keep-rejects` | off | **use it** — keeps dropped frames so you can check them |

### Why `union` and not just our own detector

Gating on our own model alone is the obvious design and it is **wrong**.
Measured 2026-09-17 on 2,224 `f2_micro_selfie` frames:

| method | kept | of total |
|---|---:|---:|
| detector only (v15 @ 0.15) | 1,083 | 48.7% |
| **union (detector OR CLIP)** | **1,896** | **85.3%** |

Sampling 150 of the detector's rejections put a drone in **81%** of them. One
frame of a man holding a quadcopter straight at the lens was thrown away, while
a frame with the same drone half-hidden behind his arm was kept.

That is the circularity trap: filtering training data with the model you intend
to improve discards exactly what the model cannot see yet. Close-up recall is a
known weak spot — v13 oversamples the close-up tail 3× for this reason — so a
detector-only gate would strip out the close-ups that fix it.

The union breakdown on that run makes the point:

```
both agreed   :  815
detector only :  113   (CLIP missed these)
CLIP only     :  968   (the detector missed these — mostly close-ups)
```

**968 frames — 51% of everything kept — would have been lost** to a
detector-only gate. CLIP is not ground truth either; it is a *differently
wrong* model, which is why pairing them works.

Spot-check `*__rejected/` before trusting any cut.

---

## 4. `dedupe` — drop near-duplicate frames

Consecutive frames from one clip are nearly identical, so this matters more here
than it did for image search. Reuse the existing per-family dedupe:

```bash
python drone_families_dataset.py dedupe \
  --families all --src gated/models --out dataset/models \
  --min-side 224 --threshold 6
```

Note it is **O(n²)** in the kept set — roughly 27 min at 50k images, 109 min at
100k. Run it once at the end, not after every fetch.

---

## 5. `report` — what is still thin

```bash
python model_frames.py report --families all
```

Per model: raw frame count, gated count, keep-rate, and a list of models with
no frames at all.

---

## A full run, in order

```bash
cd data-fetching-scripts && source ../.venv/bin/activate

python model_frames.py models                                   # 1. check the plan
python model_frames.py fetch  --families f2_micro_selfie        # 2. ONE family first
python model_frames.py gate   --families f2_micro_selfie --keep-rejects
python model_frames.py report --families f2_micro_selfie        # eyeball before scaling

python model_frames.py fetch  --families all                    # 3. then the rest
python model_frames.py gate   --families all --keep-rejects
python drone_families_dataset.py dedupe --families all \
    --src gated/models --out dataset/models --min-side 224
python model_frames.py report --families all
```

Interrupt any of these and re-run the same line to resume.

---

## Why image search was dropped

`drone_families_dataset.py browser` (bing/yandex) collected 5,822 images. Of
those, **449 were byte-identical duplicates** and the near-duplicate rate on top
is higher still — press photos of one model are the same three shots, reposted.
Filenames compounded the confusion: `_download_urls` numbers each folder from
`0001.jpg`, so 5,822 files carried only **247 distinct names** and `0001.jpg`
appeared 72 times.

Video frames do not have that failure mode. Every frame is a different instant,
angle, background and lighting condition, which is what the model needs.

The browser path still works and is left in place — it is simply not part of
this pipeline any more.

---

## Known issues in the older script

These affect `drone_families_dataset.py`, not `model_frames.py`:

- **`.done` is set on search success, not download success.** `cmd_scrape` and
  `cmd_browser` do `if urls: marker.touch()` — a query that found 200 URLs and
  saved 0 images is marked complete and never retried. `model_frames.py fetch`
  deliberately does the opposite and marks done only on frames actually written.
- **`icrawler` is not installed**, so `scrape --engines bing` fails. The browser
  path does not need it.
- **No provenance recorded.** Scraped images carry no source URL or licence.
  Worth fixing before anything ships to a client — see
  `docs/public-pretrained-models-evaluation.md` §3 for the parallel licensing
  question on the models themselves.

---

# AI triage — sort collected frames by what v16 sees

Makes hand-review cheap. It does **not** decide what is in a frame — a model cannot be its
own ground truth. If v16 picked v17's training data, v17 would only ever learn airframes
v16 already knows, and the unfamiliar silhouettes that justify collecting 150 models would
be silently discarded. This sorts and annotates; you still judge.

## The two commands

```bash
cd data-fetching-scripts

./run_ai_triage.sh triage      # 1. sort + annotate into filtered/AI Filtered v2/
                               # 2. review the annotated/ folders, delete what you reject
./run_ai_triage.sh check       # 3. DRY RUN — show what syncing would delete
./run_ai_triage.sh delete      # 4. apply it
```

The wrapper exists because this repo has three venvs and only two carry opencv:
`.venv` ✓ · `edge-motion-pipeline/.venv` ✓ · `drone-monitor/.venv` ✗ (no cv2).
Running `./ai_triage.py` directly under the wrong one fails with `ModuleNotFoundError: cv2`.
The wrapper always uses the root `.venv`.

Original collected frames under `--src` are never touched.

## Output

```
filtered/AI Filtered v2/
  1_fires_high_conf70plus/   images/  annotated/
  2_fires_mid_conf50to70/    images/  annotated/
  3_fires_low_conf25to50/    images/  annotated/
  4_silent_no_detection/     images/  annotated/
  MANIFEST.csv   README.md
```

Filenames lead with the confidence — `0.89__armattan_marmotte__..._00040.jpg`,
`MISS__..._00001.jpg` — so alphabetical order in any viewer is already priority order.

## The four quadrants — what to keep

The model splits the **columns**. Your eye decides the **rows**.

|  | model FIRES | model SILENT |
|---|---|---|
| **drone present** | **A** correct — low value, subsample every 3rd–5th | **B** MISS — 🥇 **keep all** |
| **no drone** | **C** false positive — 🥈 keep, capped ~1,000 | **D** correct — ❌ delete |

- **B is the prize.** A drone v16 cannot see is direct evidence of a gap, and closing gaps
  is the entire point of the 150-model corpus. Frames it already detects teach it little.
- **C is potent but cap it.** Over-weighting hard negatives pushes the model toward
  under-detecting.
- **D is the bulk of folder 4.** Delete freely — it costs epochs to teach what is known.
- **Folder 3 (0.25–0.50) is a hunt for missed drones, NOT a negative harvest.** See the
  confidence rule below. Worth doing yourself rather than delegating.

### The confidence rule — WITHDRAWN, judge by the scene

An earlier version of this file said to harvest negatives only at 0.50 and
above, and treated band 3 as a missed-drone hunt with nothing else kept. **That
rule is withdrawn.** It threw away exactly the frames the live cameras fail on.

Keep a frame as a negative when the scene is one our dome could really
frame — aircraft, birds, open sky, treelines, rooftops, masts, power lines,
open ground — **from any band, at any confidence**. Aircraft and birds are the
two we are shortest of and the two the live cameras get wrong most.

The score is this gate's opinion; the scene is what the model learns from. A
negative is kept with no box, Marked Null, so nothing teaches "suppress at
0.25" — the whole image simply becomes background, and a bird over open sky is
the same lesson whether it scored 0.90 or 0.26.

Still deleted at any confidence, because the dome never frames them: flight
display overlays, channel logos, desks and workbenches, cutting mats, goggles,
studio and living-room scenes, faces, keyboards, UI buttons, product shots.

Training on 0.25-level false positives teaches the model to suppress at the bottom of its
confidence range — exactly where faint distant drones live. v16 gained **+12.6 points at
0–8px** and **+3.3 at 8–12px** over v15 on 30,223 held-out RWOQ images; this is the most
direct way to hand that back.

Real band-3 false positives from f4, all at 0.25, none with a drone in the box: a bush in a
field, a picnic table, a human face, a BLHeli Cancel button, a keyboard, fence posts. None
would fire at the deployed 0.50.

**Worth watching, not acting on:** one f4 false positive boxed a human face. At 0.25 it is
harmless, but unlike UI screenshots, deployed cameras do see people. Whether v16 ever fires
on people at ≥0.50 is a question for the confuser benchmark, not for frame filtering.

## Expected volumes on f4 (15,153 frames, projected from a trial)

| band | ~frames |
|---|---|
| 1 fires high | 758 |
| 2 fires mid | 758 |
| 3 fires low | 1,136 |
| 4 silent | 12,501 |

The three fires bands (~2,650) are new review work; folder 1 is a spot-check. Folder 4 is
the filtering you were already doing by hand.

## Other families

Edit `SRC` at the top of `run_ai_triage.sh`, or call the script directly:

```bash
../.venv/bin/python ai_triage.py \
    --src "filtered/f6_heavy_lift (done)" \
    --out "filtered/AI Filtered v2 f6" \
    --model ../model-testing/models/v16-best.pt
```
