# yt-video-script

Collects drone training frames from YouTube, one aircraft model at a time, and
keeps a global record of every video it has already used so nothing is ever
collected twice.

```
search YouTube per model -> download -> cut frames -> gate -> triage
                                            |
                                            +-> recorded in video_registry.json
```

> ### The one file to know about: **`video_registry.json`**
>
> It sits at the root of this repo and holds **every YouTube video the pipeline
> has ever processed** — the URL, which category took it, which aircraft model,
> and how many frames came out. Before any download the collector asks it a
> single question: *has this video id been processed before, by anyone?*
>
> It ships filled in: **1,366 videos across 8 categories, 403,703 frames.**
> Clone the repo and the collector already knows to skip all of that and go
> looking for new material.
>
> **Commit and push it after every collection run.** It is shared state. If two
> people collect without pushing it, they both download the same videos.

---

## Quickstart

```bash
git clone https://github.com/ABIDULLAH786/yt-video-script.git
cd yt-video-script

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
sudo apt install ffmpeg nodejs          # or your platform's equivalent

./video_registry.py stats               # what has already been collected
./model_frames.py models --families 6   # what would be collected, no network

# collect — 2 new videos per search term, 3 terms per model
./model_frames.py fetch --families 6 --top-up 2 --cookies-from-browser chrome

git add video_registry.json && git commit -m "Collect f6 top-up" && git push
```

Needs Python 3.10+, `yt-dlp`, `ffmpeg` and Node (for yt-dlp's JS challenge
solver).

---

## The registry

Three rules it enforces, each of which cost real time before it existed.

**1. A video already taken by another category is skipped.** YouTube returns the
same clip for searches of quite different aircraft. On one measured run 133
videos had landed in more than one model directory and 38 of those spanned
categories, one video sitting in nine directories at once. The second copy is
the same footage, and it is the wrong aircraft for at least one of the
categories holding it. The check is **category-blind on purpose**.

**2. The record outlives the frames.** This used to be worked out by scanning
frame filenames on disk, which stops working the moment triage moves or deletes
them — and it always does. Five of seven categories had their raw folders
cleared after triage, so a re-run would have re-downloaded every video in them.

**3. It is written after every single video, not at the end of a run.** Runs end
badly more often than they end cleanly: a YouTube rate-limit aborts the process,
and Ctrl-C and a full disk are just as common. Whatever was downloaded before
that point is never downloaded again.

Writes are temp-file-then-rename, and temps abandoned by a killed process are
swept on the next save, so the file stays valid JSON through a hard kill. That
is tested against 200 kills mid-write.

### What it looks like

```json
{
  "version": 1,
  "updated_utc": "2026-10-21T06:47:52Z",
  "counts": {"videos": 1366, "processed": 1366, "frames": 403703, "categories": 8},

  "categories": {
    "f1_folding_quad": {
      "videos": 177,
      "frames": 82338,
      "urls": ["https://www.youtube.com/watch?v=p6aNfWdPqJQ", "..."]
    }
  },

  "videos": {
    "p6aNfWdPqJQ": {
      "url": "https://www.youtube.com/watch?v=p6aNfWdPqJQ",
      "category": "f1_folding_quad",
      "model": "dji_neo",
      "frames": 333,
      "status": "processed",
      "processed_utc": "2026-10-21T06:47:50Z"
    }
  }
}
```

`videos` is the source of truth. `categories` is rebuilt from it on every save
purely so a human can read the URL list per category — **edit `videos`, never
`categories`**, or the change is overwritten on the next write.

### What is in it now

| category | videos | frames |
|---|---:|---:|
| f1_folding_quad | 177 | 82,338 |
| f2_micro_selfie | 152 | 52,842 |
| f3_ducted_fpv | 177 | 68,664 |
| f4_open_frame_fpv | 156 | 7,742 |
| f5_enterprise_quad | 167 | 57,170 |
| f6_heavy_lift | 146 | 64,484 |
| f6_heavy_lift_v2 | 141 | 44,845 |
| f7_fixed_wing_vtol | 250 | 25,618 |
| **total** | **1,366** | **403,703** |

### Working with it

```bash
./video_registry.py stats                          # counts per category
./video_registry.py check <url-or-id> [...]        # would this be skipped?
./video_registry.py list --category f1_folding_quad
./video_registry.py export --csv videos.csv
./video_registry.py remove <url-or-id>             # collect it again
./video_registry.py add <url> --category f6_heavy_lift
```

`check` is the one to reach for when a video you expected did not download:

```
$ ./video_registry.py check https://www.youtube.com/watch?v=Ge7r7hbWCds dQw4w9WgXcQ
Ge7r7hbWCds: processed in f6_heavy_lift_v2/dji_agras_t25_t25p (1472 frames, ...) — would be SKIPPED
dQw4w9WgXcQ: NEW — would be collected
```

A video that failed to download (private, removed, geo-blocked) is recorded too.
It is retried once, then skipped — `--retry-failed` overrides that.

---

## 1. Collect

```bash
./model_frames.py models --families 6              # list, no network
./model_frames.py fetch   --families 6 --videos-per-term 3
./model_frames.py fetch   --families 6 --top-up 2  # only videos never seen before
```

Categories are `f1_folding_quad` … `f7_fixed_wing_vtol`, and `--families` takes
`6`, `f6`, `f6_heavy_lift`, `1,3,6` or `all`. The model list per category comes
from `drone-model-checklist-by-family.md`, which is the document to edit when
adding an aircraft — the code parses it rather than keeping its own copy.

| flag | what it does |
|---|---|
| `--videos-per-term N` | videos to keep per search term (3 terms per model) |
| `--top-up N` | revisit every model, take N *new* videos each |
| `--oversample N` | search N× deeper than needed, then keep the first that pass the duration filter. Without it the duration filter eats the quota — a measured run requested 60 videos and got 20, with five models getting nothing |
| `--min-duration` / `--max-duration` | seconds; skips Shorts and hour-long compilations |
| `--fps 0.5` / `--max-side 1280` | frames per second of video, longest edge in px |
| `--registry PATH` | use a different registry file |
| `--retry-failed` | also retry videos marked failed |
| `--no-resume` | ignore the registry and re-download everything. Rarely what you want — use `--top-up` |
| `--variant SUFFIX` | write to `f6_heavy_lift_v2` instead of `f6_heavy_lift` |

### Search terms

Three per model, in `MODEL_TEMPLATES`:

```
{model} flight test
{model} takeoff and landing
{model} hands on outdoor
```

All three describe *ground-filmed* video, deliberately. **Do not add "footage",
"cinematic", "4K", "POV" or "FPV view".** Those are the words people put on
video shot *from* the aircraft, where the drone is never in frame — the single
biggest source of waste in this pipeline.

### When YouTube blocks you

After a few hundred downloads from one IP you get *"Sign in to confirm you're
not a bot"*. The script detects it, says so once and stops rather than grinding
through hundreds of identical failures.

```bash
./model_frames.py fetch --families 6 --cookies-from-browser chrome \
    --sleep-requests 2 --sleep-interval 5
```

Close the browser first — it locks its own cookie database. The block is
triggered by request *rate* from one IP, so spreading the same work over more
time is the only thing that actually avoids it.

---

## 2. Gate — drop frames with no drone in them

Two kinds of video survive the search and are useless: one *about* a drone (a
review, a person at a desk) and one shot *from* the drone (the camera is the
aircraft, so it is never in frame). Both are removed by the same test — is an
aircraft visible in this frame?

```bash
./model_frames.py gate --families 6 --keep-rejects
```

It keeps a frame when **either** a drone detector **or** CLIP sees a drone, and
drops only what both reject. That is not belt-and-braces: gating on our own
detector alone threw away 81% of what it rejected, measured by hand on 150
sampled frames.

| flag | what it does |
|---|---|
| `--method union\|detector\|clip` | `union` is the default and the right one |
| `--conf` | detector threshold, deliberately low |
| `--clip-threshold` | summed probability of the drone-present prompts |
| `--keep-rejects` | keep dropped frames so you can eyeball them. Strongly advised |
| `--warn-rate` | flag any model whose keep-rate falls below this |

---

## 3. Triage and review

```bash
./ai_triage.py                                           # bucket by confidence
./ai_triage_sync.py --out "<category folder>"            # dry run
./ai_triage_sync.py --out "<category folder>" --apply    # mirror deletions
```

`ai_triage.py` sorts frames into four confidence bands, each holding an
`annotated/` copy (detector box burned in, easy to judge) and an `images/` copy
(clean). You review `annotated/` and delete what you do not want;
`ai_triage_sync.py` then removes the matching file from `images/`.

**Never upload `annotated/`.** Those JPEGs have the boxes burned into the
pixels; a model trained on them learns "red rectangle = drone". Upload from
`images/`.

Both sync scripts are a dry run by default and refuse to act on a band whose
`annotated/` folder is empty, or whose filenames do not appear in that
category's `MANIFEST.csv`. That second guard exists because its absence
destroyed 33,889 reviewed frames in one run: the folder in place belonged to a
different category, 0% of the names matched, and the script read that as "every
frame was rejected".

If you reviewed `images/` by mistake, the decisions are still real — mirror them
the other way with `reverse_sync.py` rather than redoing the work.

---

## 4. Mine hard negatives

```bash
./model_frames.py mine --families 6 --only dji_agras_t50
```

Runs the current model at the production threshold over the frames a person
*rejected*, and keeps only the ones it false-fires on. Those are the frames
worth adding as negatives; the rest teach nothing. `--kept` names the
hand-filtered directory, whose filenames are excluded so the candidates are
exactly the rejects.

---

## Scripts

| script | does |
|---|---|
| `model_frames.py` | `models` / `fetch` / `gate` / `mine` / `report` |
| `video_registry.py` | the global video record |
| `ai_triage.py` | bucket frames by detector confidence |
| `ai_triage_sync.py` | mirror `annotated/` deletions onto `images/` |
| `reverse_sync.py` | the other direction, for when you reviewed `images/` by mistake |
| `dedupe_categories.py` | remove frames whose video lives in another category |
| `video_index.py` | per-category and global CSVs of the videos behind the frames |
| `rebuild_frames.py` | re-download and re-cut from a manifest, to recover deleted frames |
| `extract_video_frames.py` | frames from a local video file |

`./model_frames.py report --families 6` prints per-model counts so gaps stay
visible.

---

## Adding a category or an aircraft

Edit `drone-model-checklist-by-family.md` — add a row to the right table. The
collector reads that file, so nothing in the code changes. For a new category,
add a `## N. name` heading and register the key in `FAMILY_KEYS` in
`model_frames.py`.
