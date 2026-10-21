# yt-video-script

Collects drone training frames from YouTube, one aircraft model at a time, and
keeps a global record of every video it has already used so nothing is ever
collected twice.

```
search YouTube per model  ->  download  ->  cut frames  ->  record the video
                                                            in video_registry.json
```

---

## The registry is the point

`video_registry.json` is the one file that matters. It holds every YouTube video
the pipeline has ever processed, which category took it, and how many frames
came out. Before any download the collector asks it one question — **has this
video id been processed before, by anyone?** — and skips it if the answer is
yes.

**It ships already filled in: 1,366 videos across 8 categories, 403,703 frames.**
That is the work already done on this project. Clone the repo and the collector
immediately knows to skip all of it and go looking for new material.

Three rules it enforces, each of which cost us real time before it existed:

**1. A video already taken by another category is skipped.** YouTube returns the
same clip for searches of quite different aircraft. On one measured run 133
videos had landed in more than one model directory and 38 of those spanned
categories, one video sitting in nine directories at once. The second copy is
the same footage, and it is the wrong aircraft for at least one of the
categories holding it. The check is category-blind on purpose.

**2. The record outlives the frames.** The collector used to work this out by
scanning frame filenames on disk, which stops working the moment triage moves or
deletes them — and it always does. Five of our seven categories had their raw
folders cleared after triage, so a re-run would have re-downloaded every video in
them.

**3. It is written after every single video, not at the end of a run.** Runs end
badly more often than they end cleanly: a YouTube rate-limit aborts the process,
and Ctrl-C and a full disk are just as common. Whatever was downloaded before
that point is never downloaded again.

The file is written temp-file-then-rename, so it is always valid JSON even if
the machine is killed mid-write. That is tested against 200 hard kills.

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
`categories`**, or your change will be overwritten.

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

**Commit the registry after a collection run.** It is the shared state; if two
people collect without pushing it, they will both download the same videos.

---

## Setup

Needs Python 3.10+, `yt-dlp`, `ffmpeg`, and Node (for yt-dlp's JS challenge
solver).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
sudo apt install ffmpeg nodejs        # or your platform's equivalent
```

---

## Collecting

```bash
# what would be collected, no network
./model_frames.py models --families 6

# collect: 3 videos per search term, 3 terms per model
./model_frames.py fetch --families 6 --videos-per-term 3

# top up a category that is already done — every model is revisited, but only
# videos the registry has never seen are downloaded
./model_frames.py fetch --families 6 --top-up 2
```

Categories are `f1_folding_quad` … `f7_fixed_wing_vtol`, and `--families` takes
`6`, `f6`, `f6_heavy_lift`, `1,3,6` or `all`. The model list per category comes
from `drone-model-checklist-by-family.md`, which is the document to edit when
adding an aircraft — the code parses it rather than keeping its own copy.

### When YouTube blocks you

After a few hundred downloads from one IP you get *"Sign in to confirm you're
not a bot"*. The script detects this, says so once and stops rather than
grinding through hundreds of identical failures.

```bash
./model_frames.py fetch --families 6 --cookies-from-browser chrome \
    --sleep-requests 2 --sleep-interval 5
```

Close the browser first — it locks its own cookie database. The block is
triggered by request *rate* from one IP, so spreading the same work over more
time is the only thing that actually avoids it.

### Flags worth knowing

| flag | what it does |
|---|---|
| `--videos-per-term N` | videos to keep per search term (3 terms per model) |
| `--top-up N` | revisit every model, take N *new* videos each |
| `--oversample N` | search N× deeper than needed, then keep the first that pass the duration filter. Without it the duration filter eats the quota — a measured run requested 60 videos and got 20, with five models getting nothing |
| `--min-duration` / `--max-duration` | seconds; skips Shorts and hour-long compilations |
| `--fps 0.5` | frames per second of video |
| `--max-side 1280` | longest edge, px |
| `--registry PATH` | use a different registry file |
| `--retry-failed` | also retry videos marked failed (private, removed, geo-blocked). Off by default so a dead video is not re-attempted every run |
| `--no-resume` | ignore the registry entirely and re-download everything. Rarely what you want — use `--top-up` |
| `--variant SUFFIX` | write to `f6_heavy_lift_v2` instead of `f6_heavy_lift` |

### Search terms

Three per model, in `MODEL_TEMPLATES`:

```
{model} flight test
{model} takeoff and landing
{model} hands on outdoor
```

All three describe *ground-filmed* video, and that is deliberate. **Do not add
"footage", "cinematic", "4K", "POV" or "FPV view".** Those are the words people
put on video shot *from* the aircraft, where the drone is never in frame — the
single biggest source of waste in this pipeline.

---

## After collecting

```bash
./ai_triage.py          # sort frames into 4 confidence bands with a detector
./ai_triage_sync.py --out "<category folder>"            # dry run
./ai_triage_sync.py --out "<category folder>" --apply    # mirror deletions
```

You review the `annotated/` copies — they carry the detector's box, so the call
is usually obvious — and delete the frames you do not want. `ai_triage_sync.py`
then removes the matching file from `images/`, which is the clean copy that
actually gets uploaded for labelling.

**Never upload `annotated/`.** Those JPEGs have the boxes burned into the
pixels; a model trained on them learns "red rectangle = drone".

Both sync scripts are a dry run by default and refuse to act on a band whose
`annotated/` folder is empty, or whose filenames do not appear in that
category's manifest. That second guard exists because its absence destroyed
33,889 reviewed frames in one run: the folder in place belonged to a different
category, 0% of the names matched, and the script read that as "every frame was
rejected".

| script | does |
|---|---|
| `model_frames.py` | search, download, cut frames |
| `video_registry.py` | the global video record |
| `ai_triage.py` | bucket frames by detector confidence |
| `ai_triage_sync.py` | mirror `annotated/` deletions onto `images/` |
| `reverse_sync.py` | the other direction, for when you reviewed `images/` by mistake |
| `dedupe_categories.py` | remove frames whose video lives in another category |
| `video_index.py` | per-category and global CSVs of the videos behind the frames |
| `rebuild_frames.py` | re-download and re-cut from a manifest, to recover deleted frames |
| `extract_video_frames.py` | frames from a local video file |

---

## Adding a category or an aircraft

Edit `drone-model-checklist-by-family.md` — add a row to the right table. The
collector reads that file, so nothing in the code needs to change. For a new
category, add a `## N. name` heading and register the key in `FAMILY_KEYS` in
`model_frames.py`.
