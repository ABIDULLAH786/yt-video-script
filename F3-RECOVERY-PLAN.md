# f3_ducted_fpv — recovery plan

Written 2 October 2026, after `ai_triage_sync.py --apply` deleted 33,889 clean
frames from `filtered/AI Filtered/f3_ducted_fpv/*/images/`.

---

## What happened

`ai_triage_sync.py` mirrors deletions from `annotated/` onto `images/`: anything
in `images/` whose twin in `annotated/` is gone gets deleted.

At the time it ran, **bands 1, 2 and 3 of f3's `annotated/` held f6 heavy-lift
files, not f3 files**:

```
f3/1_fires_high_conf70plus/annotated -> 0.70__dji_flycart_100__…      f6
f3/2_fires_mid_conf50to70/annotated  -> 0.50__dji_agras_t25_t25p__…   f6
f3/3_fires_low_conf25to50/annotated  -> 0.25__dji_agras_t100__…       f6
f3/4_silent_no_detection/annotated   -> MISS__axisflying_manta__…     f3, correct
```

So for bands 1–3 **nothing** in `annotated/` matched the f3 manifest. The script
read a zero-percent match as "every frame was rejected" and deleted all of them.
Band 4 matched and behaved correctly, keeping 312.

```
band                              keep  delete
1_fires_high_conf70plus              0    6608
2_fires_mid_conf50to70               0    2161
3_fires_low_conf25to50               0    2458
4_silent_no_detection              312   22662
TOTAL                              312   33889
```

The direction of the sync was checked by comparing file COUNTS only. Nobody
checked that the filenames corresponded. A 0% name match is a mismatch, not a
review decision, and the script should refuse it the same way it refuses an
empty folder. **That guard is still not in place — add it before syncing again.**

---

## Current state

| item | state |
|---|---|
| `images/` bands 1–3 | **gone** — 11,227 clean frames |
| `images/` band 4 | 312, intact, the real review |
| `annotated/` bands 1–3 | 6,608 / 2,161 / 2,458 — restored from Trash, 100% manifest match |
| `annotated/` band 4 | 313, intact |
| Trash | emptied |
| `MANIFEST.csv` | **intact — this is what makes recovery possible** |
| `video_index/f3_ducted_fpv.csv` | 181 video ids |
| `_quarantine_f6_files_wrongly_here/` | 3,449 misplaced f6 files, kept not deleted |

### The USB backup only covers band 4

`/media/khive/0AA0-C5FC/f3_ducted_fpv annotated` holds 2,067 / 730 / 652 / 313,
but bands 1–3 of it are the same **f6** files. That review was done against f6
images and cannot be applied to f3. Only its band 4 is genuine f3, and that is
already back in place.

**Consequence: the f3 review for bands 1–3 has to be done again.** No script
recovers it, because it was never performed on f3 frames.

---

## Recovery

Frames are reproducible. Extraction is deterministic — same video, same fps,
same scale, same frame number — and this was verified byte-for-byte rather than
assumed: a re-derived frame came back as md5 `4cf519bb838afe635dbaf2e6f8355f90`,
identical to the surviving original.

### Step 1 — rebuild the original frames

`rebuild_frames.py` reads `MANIFEST.csv`, maps every frame back to its video id,
term and frame number, downloads each video once, re-extracts at the same
settings (`fps=0.5`, `scale min(1280,iw)`, `-q:v 2`) and writes the files back
under their exact original names.

```bash
./rebuild_frames.py --category f3_ducted_fpv            # plan, no downloads
./rebuild_frames.py --category f3_ducted_fpv --apply \
    --cookies-from-browser chrome --sleep-interval 5
```

- 181 videos, 34,201 frames -> `raw/models/f3_ducted_fpv/<model>/`
- resumable per video; re-issue the same command after any interruption
- `--only-missing` skips frames already on disk
- stops cleanly on a YouTube rate-limit instead of grinding through failures
- a video feeding several model folders is downloaded once and fanned out

Expect a few hours unattended. Close Chrome before using `--cookies-from-browser`.

### Step 2 — re-bucket by confidence

```bash
./ai_triage.py     # against the rebuilt raw/models/f3_ducted_fpv
```

Same model (v16) and same thresholds, so the four bands reproduce as before.

### Step 3 — re-apply the review that survives

Only band 4 has valid f3 decisions (312 frames). Match them by filename against
the fresh band 4 and delete the rest of that band. **Bands 1–3 need reviewing
from scratch.**

---

## Before running any sync again

1. **Add the name-match guard to `ai_triage_sync.py`.** It must refuse a band
   where `annotated/` and the manifest share no filenames, and print what it
   found instead of deleting.
2. **Check the direction by NAMES, not counts.** Compare a sample of
   `annotated/` filenames against `MANIFEST.csv` before trusting either sync
   script.
3. **Always dry-run first.** Both sync scripts default to a dry run; read the
   per-band plan and sanity-check the keep/delete split against what you
   actually did.
4. **Keep the Trash until the result is verified.** It was the only reason the
   annotated frames came back this time.
