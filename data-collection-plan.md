# Data collection plan — general detector (v16+)

Companion to `drone-model-checklist-by-family.md` (what to collect) and
`drone_families_dataset.py` (how to collect it). This file is **how much, in what order,
and what to do with it**.

> **Scope.** General detector, the v9→v15 line. NOT the `dji-neo/` track — v2 there is
> deliberately one room, one airframe, and must not be fed this data.

---

## 1. Targets

### Positives

The stated target was ~500 per **model**. Changing the unit: **~500 per distinct
silhouette**, not per model name.

Reason: a Mini 3 and a Mini 4 Pro differ by details no detector resolves at 100m. 500 each
buys ~500 images of information and ~500 of redundancy. The checklist counts ~30 distinct
silhouettes across the seven families, so:

| | count | images |
|---|---|---|
| distinct silhouettes | ~30 | |
| target per silhouette | 500 | **~15,000** |
| existing outdoor positives (v15) | | ~52,600 |

Per silhouette, spread across:

- **distance** — fills frame · mid · distant · barely-visible speck
- **angle** — below, side, three-quarter, head-on, top-down
- **background** — open sky · against buildings · against trees · indoor
- **lighting** — daylight · overcast · dusk · backlit against bright sky

**Distance and background matter more than model identity.** The v14→v15 failure was a
background-domain problem — zero indoor images in 52,600 positives — not a missing-model
problem. A 21st folding quad would not have touched it.

### Negatives

Stated target was ~10K against ~80K positives, which is **11%**. That is below the 12%
floor already enforced as `NEG_FRACTION_MIN` in v13/v15, and far below what suppresses
false alarms on a model with this FP history.

**Raise negatives to 20–25% of the total.**

| category | target | why |
|---|---|---|
| Birds | 1,500–2,000 | the classic confuser; already partly covered |
| Aircraft: airplanes, jets, helicopters, seaplanes, gliders | 1,500 | plane-shaped ≠ drone (see §6) |
| Lighter-than-air: hot air balloons, blimps, weather balloons | 500 | |
| Parachutes, kites, paragliders | 500 | |
| Ground/indoor confusers: bags, ceiling fans, sprinkler heads, wall sensors, dark spots, holes, light fixtures | 1,000 | the v14 wall-FP class |
| **Empty backgrounds** — clear sky, bare building facades, treelines, empty rooms, empty ceilings | **4,000+** | the single most valuable negative type |
| **Total** | **~10,000** | pairs with ~35–40K positives in batch 1 |

**Empty backgrounds are the highest-value negatives and the easiest to over-collect.** For
every scene where a drone appears, the same scene without one is worth more than a generic
negative from elsewhere.

---

## 2. Question 1 — one shot, or train / test / retrain?

**Iterative, without question.** And the split should not be "half the images now, half
later" — it should be "a baseline now, then whatever the failures demand."

### Why iterative, from this project's own record

| version | what was added | outcome |
|---|---|---|
| v12 | VisioDECT + FGVC, thousands of images, chosen up-front | **underperformed** |
| v13 | DUT + distant-drones, chosen to fix a measured gap | **clean win** |
| v15 | 2,466 indoor images, chosen to fix a measured failure | **wall FPs −91%, drone detections +66%** |

v15's win came from **2,466 targeted images** against a 52,600-image base. Volume was not
what fixed it; aim was. Collecting 70,000 images before the first test would have cost weeks
and would not have found the indoor gap any faster — the failure video found it in an
afternoon.

### The hard constraint you have not hit yet

v15 trained ~60K images for 30 epochs in **10.1 hours** on 2×T4. Scaling:

| dataset | est. time, 30 epochs | fits Kaggle's 12h session? |
|---|---|---|
| 60K (v15) | 10.1h | just |
| **90K (your plan)** | **~15h** | **no** |
| 40K (batch 1 below) | ~7h | yes, comfortably |

At 90K you would have to resume across sessions every single run. That turns a one-day
iteration into a three-day one, and you would be doing it before knowing whether the data
helps.

### Recommended phasing

**Batch 1 — baseline, ~40K total**
- Existing v15 data, replay-sampled down (see below)
- ~150–200 per silhouette for the gap families (f3, f4, f6) → ~5,000 new positives
- ~10,000 negatives per the table above
- Train 30 epochs, ~7h, one session

**Gate — measure before collecting more**
- `model-testing/` against the 6,149-image outdoor benchmark (**still never run — do this first, on v15, before v16 starts**)
- The failure videos through `/detect`
- Per-family recall from the held-out clips

**Batch 2 — targeted at what batch 1 got wrong**
- Only now decide what the remaining ~10K images should be
- If FPV quads are missed at distance → more distant FPV, not more FPV close-ups
- If a background still fires → paired empty scenes of exactly that background

**Batch 3+** — repeat. Each round should be smaller and more specific than the last.

### On the replay subset

You do not need all 52,600 outdoor positives every run. Lower the per-source caps in
`SOURCES` to keep every domain represented at ~25% volume — that alone brings training back
inside one session while preserving the prior. This is the same reasoning as the earlier
"can I drop the old datasets" question: a representative slice prevents forgetting; the full
set is not required.

---

## 3. Question 2 — smart augmentation: new data or not?

**Not new data, and mostly not worth doing offline.**

### It is already happening at training time

Your Cell 3 already runs, every epoch:

```python
scale=0.4, mosaic=1.0, fliplr=0.5,
hsv_h=0.015, hsv_s=0.7, hsv_v=0.4,
```

plus Ultralytics' built-in albumentations, visible in your own training log:

```
Blur(p=0.01), MedianBlur(p=0.01), ToGray(p=0.01), CLAHE(p=0.01)
```

Every image is already seen flipped, scaled, colour-shifted and mosaicked across epochs.
Pre-generating those variants and uploading them means **paying to store and download
transformations the trainer would produce for free.**

### Why it actively hurts

1. **It inflates the count without adding information.** 3× augmented copies of 30K images
   is not 90K images; it is 30K images and a bigger bill.
2. **It breaks the split.** An image and its flipped copy are near-identical. Roboflow's
   random split puts one in train and one in valid, and your validation score becomes a
   memorisation score — the exact leak the v2 clip-level split exists to prevent.
3. **It competes with training-time augmentation.** Pre-flipped images still get flipped
   again at train time, so the effective augmentation is doubled and uncontrolled.

Roboflow's version-generation augmentation (the 3× multiplier) is the specific trap here.
**Generate versions at 1× — no augmentation.**

### The one case where it is right

When you need an augmentation Ultralytics does **not** apply, that matches your actual
deployment condition. The obvious candidate: **PTZ slew blur.** Your log shows blur at
`p=0.01` — 1% of images. The 1336px false box came from motion blur, so that is arguably
far too low for your domain.

**Tune the training parameter rather than pre-baking images:**

```python
# in model.train(), if PTZ blur is a priority
augment=True,
# and raise Ultralytics' blur probability via a custom albumentations pipeline,
# or simply collect more genuinely-blurred frames — which you already do
```

Real blurred frames from the real camera beat synthetic blur every time. You already
established that when you kept the blurred frames instead of deleting them.

### Absolute rule

**Never augment the validation or test split.** Augmented copies of training images landing
in eval turns the score into fiction. The augmentation belongs in the training loop, where
it cannot reach the eval set by construction.

---

## 4. Question 3 — crop and resize?

### Resize: yes, with a floor

YOLO letterboxes everything to `imgsz=640` before the model sees it, so a 4000px source and
a 1280px source are identical by the time they reach the network.

- **Do** downscale huge sources to ~1280–1920 on the long edge. Saves storage, download time
  and Roboflow quota, changes nothing for the model.
- **Do not** resize below 1280. It forecloses training at `imgsz=1280` later, which is the
  standard lever for small-object recall — and small distant drones are exactly your weak
  case.
- **Never upscale.** It adds no detail and teaches the model to expect interpolation
  artefacts.

Measured on your own data: Batch1/Batch2 at 2592px and Batch3 at 1280px had medians of 1126
and 128 Laplacian variance at native size — but after resizing both to 640, the difference
largely vanished. The source resolution mattered far less than it appeared.

### Crop: careful — it changes the scale distribution

Cropping in to make a small drone bigger **teaches the model the wrong apparent size.** If
deployment shows the drone at 20 pixels and training shows it at 200, that mismatch costs
recall precisely where you need it.

| use | verdict |
|---|---|
| Ad-hoc cropping to "make the drone clearer" | **No.** Distorts the scale distribution. |
| Cropping out irrelevant borders / letterbox bars | Fine |
| Systematic tiling (e.g. SAHI) for small objects | **Yes — but apply at inference too** |

Tiling is a legitimate and strong technique for tiny-object detection: split each frame into
overlapping tiles, detect per tile, merge. It genuinely helps distant drones. But it only
works if training and inference use the same scheme. **Half-adopting it — tiled training,
full-frame inference — is worse than not doing it at all.**

If you crop in Roboflow, it adjusts the labels for you. If you crop outside Roboflow, the
bounding boxes must be recomputed or the labels are silently wrong.

---

## 5. Training resolution and the scale distribution

The `imgsz` decision is **not** made from the collected dataset. It is made from what the
drone looks like **at inference on your cameras**, and the dataset is then shaped to match.
Getting this backwards is how v9 ended up with a close-range failure mode.

### The only formula you need

YOLO letterboxes to a square, so:

```
box_pixels = normalized_long_edge × imgsz
```

A box with normalized long edge 0.085 is 54px at `imgsz=640`, 109px at 1280. Against YOLO's
finest detection head (stride 8, an 80×80 grid at 640):

| box size after resize | detectability |
|---|---|
| under 10px | effectively invisible — no grid cell owns it |
| 10–16px | marginal, unreliable |
| 20px+ | comfortable |

### The trap in scraped data

**Web images skew heavily toward large subjects.** Product shots, press photos and reviews
all frame the drone to fill the frame. Scrape 15,000 images across the seven families and the
box-size distribution will centre somewhere around 0.2–0.5 normalized — that is **128–320px
at imgsz 640.**

Deployment is the opposite. A drone at operational range on the PTZ is tens of pixels
native, which at `imgsz=640` from a 2592-wide sensor becomes single digits.

Train on the scraped distribution unmodified and you get a model that is excellent at
identifying a drone someone is holding, and blind to one in the sky at 100m. That is the v9
close-range failure repeating, and neither more models nor a bigger `imgsz` fixes it — the
data is simply about the wrong thing.

### What to do instead

**Step 1 — measure deployment scale first.** From your own camera footage, not from scraped
images. Label a handful of frames at the ranges the client actually cares about and compute
the normalized box sizes. That distribution is the target.

**Step 2 — shape the collection to match it.** Treat scale as a collection axis with its own
quota, exactly like angle and lighting:

| apparent size | share of positives | source |
|---|---|---|
| large (>60px @640) — fills frame, held, close pass | ~25% | product/press shots, close footage |
| medium (20–60px @640) | ~40% | typical flight photos, mid-range footage |
| **small (10–20px @640)** | **~35%** | **distant sky shots, surveillance footage, own captures** |

Small is the hardest quota to fill from the web and the most valuable. Expect to generate
much of it yourself rather than scrape it.

**Step 3 — reject what cannot be learned.** Drop any image where the drone is under ~8px at
your chosen `imgsz`. It cannot be labelled reliably, and a box around noise teaches noise.

**Step 4 — then pick `imgsz`** so the deployment-critical sizes land above 16px.

### The cost of going above 640

| | 640 | 960 | 1280 |
|---|---|---|---|
| compute per image | 1× | 2.25× | **4×** |
| v15-scale run (10.1h at 640) | 10.1h | ~23h | **~40h** |
| fits Kaggle's 12h session | yes | no | no |
| memory at batch 16 on a 14.9GB T4 | ~4.5GB | ~10GB | **OOM** |
| inference cost on the edge | 1× | 2.25× | 4× |

The inference row is the binding constraint. The edge runs live RTSP detection feeding the
PTZ tracking loop; 4× inference cost risks breaking the real-time budget that loop depends
on.

Note also that `imgsz` is a single integer — you do not train at "1280×960". A 1280×960
frame letterboxes into 1280×1280, and the 960 buys nothing on its own.

### Two cheaper levers than raising imgsz

**`multi_scale=True`** — currently `multi_scale=0.0`. Varies image size ±50% during training,
buying scale robustness at near-zero cost. Worth enabling regardless of what else is decided.

**Motion-gated cropping at inference.** The edge pipeline **already runs MOG2 motion
gating** — that is a region proposal in all but name. Crop around the motion region and run
detection on the crop, and a distant drone becomes large within that crop. This gets
small-target recall at `imgsz=640` without paying 4× anywhere, and reuses infrastructure that
already exists. Strictly better than raising `imgsz` for the long-range case, provided the
crop is applied consistently.

### Acceptance gate

Before any batch trains, print the box-size distribution and check the p10 lands above 16px
at the chosen `imgsz`. If it doesn't, the fix is usually the collection mix, not the
resolution.

### DECIDED 2026-09-15 — settled by measurement, not preference

Three questions, answered from the benchmarks in
`model-testing/results/BENCHMARK-2026-09-15.md`.

**Source image resolution: minimum 1280 px long edge, maximum ~1920 px.**

Below the training `imgsz` Ultralytics *upscales*, which adds no information and teaches
interpolation artefacts. The floor is set at 1280 rather than 640 so training at 1280 stays
possible later without recollecting. Above ~1920 there is no gain — everything letterboxes
down regardless — so downscale on ingest and save the storage and Roboflow quota.

**But resolution is the wrong knob.** What matters is the drone's pixel count, which is
independent of image size: a 4000px photo of a distant speck is worth less than a 1280px
photo of a drone at 40px. The binding rule is **reject any image where the drone is under
15px in the source**, at any resolution. Below that the box is guesswork, and a box around
haze teaches the model to detect haze.

**Training resolution: 640, with `multi_scale=True`.**

| | 640 | 1280 |
|---|---|---|
| compute | 1x | 4x |
| fresh run on a large set | fits one Kaggle session | ~40h, needs multi-session resume |
| proven here | yes (v15) | untested |

Decided because **train-at-640 / infer-at-1280 already works**: v15 trained at 640 and
gained 85.4% -> 95.5% recall purely from being run at 1280. That captures most of the
high-resolution benefit without the training cost. A fresh COCO start also needs far more
epochs than a fine-tune (Neo v1 used 120 against v13-v15's 30), which makes 4x harder still.

`multi_scale` is currently `0.0`. Turning it on varies image size +-50% during training at
near-zero cost and directly targets the train/infer mismatch. Cheapest real gain available.

**Aspect ratio: train square, infer rectangular, do NOT restrict collection to 4:3.**

`[960, 1280]` is valid - both multiples of 32, and already what the edge and the TensorRT
engine use. Keep it **for inference**. Do not train on it:

- The 7-category data is heterogeneous by nature - web scraping yields 16:9, 3:2, 4:3 and
  portrait. A fixed 4:3 canvas pads portrait images severely and fits almost nothing
  exactly. Square is the correct canvas for mixed shapes, which is why it is the default.
- Collecting 4:3 only would gut the dataset. Most drone photography online is not 4:3;
  that trades a large share of available images for padding that costs little at training.
- There are two camera geometries in play anyway - Milesight 2592x1944 (4:3) and the
  1920x1080 sources (16:9). Training to one mismatches the other.

Square where the data is varied, rectangular where the camera is fixed. That split is
already in production and is correct.

**Revisit if:** the box-size audit shows p10 below 16px at 640 (then train at 1280 and
budget for multi-session resume), or inference moves to native-resolution crops (then the
whole scale quota changes - see `future_tasks/crop-detect-native-resolution-inference.md`).

---

## 6. Budget summary

| | batch 1 | later batches |
|---|---|---|
| new positives | ~5,000 (gap families) | decided by measured failures |
| replayed v15 positives | ~13,000 (25% sample) | same |
| negatives | ~10,000 | targeted at observed FPs |
| **total** | **~28,000–40,000** | smaller each round |
| est. training time | ~7h, one Kaggle session | same or less |

Compare to collecting ~90,000 up front: three weeks of scraping and labelling, a run that no
longer fits one session, and no information about whether any of it addresses a real failure.

---

## 7. Blocked: the fixed-wing class definition

Family 7 (fixed-wing and VTOL) **cannot be collected until this is decided.**

The model currently learns that plane-shaped means *not a drone* — the Marked-Null airplane,
glider and helicopter confusers in `dji-neo-dataset` are deliberate and correct. Adding eBee,
Zipline and WingtraOne as **positives** while those remain **negatives** trains the model in
two directions on the same visual features.

Three options, all defensible, one decision required:

1. **Exclude fixed-wing UAVs from the class.** Simplest. The detector means "rotorcraft
   drone". Client must accept that a fixed-wing UAV will not be flagged.
2. **Include them, and narrow the confusers.** Keep manned aircraft as negatives, add
   unmanned fixed-wing as positives. Requires re-auditing every existing aircraft negative
   to confirm none are UAVs — a real labelling cost.
3. **Two classes.** `rotorcraft` and `fixed-wing-uav`. Cleanest signal, but breaks the
   single-class architecture the whole v7→v15 line is built on, and the 5-class experiment
   already failed once.

**This is a client/product decision, not a data one.** Families 1–6 can proceed now.

---

## 8. Acceptance gates per round

Before any collected batch reaches training:

- [ ] Roboflow version generated at **1× — no augmentation**
- [ ] No image appears in two splits (Cell 2b asserts this)
- [ ] Negatives are 20–25% of the total
- [ ] Every split has both positives and negatives
- [ ] CLIP relevance filter run, and the **rejects folder eyeballed** — the keep/drop counts
      alone cannot tell you it worked

Before any trained model is deployed:

- [ ] Beats the incumbent on the **6,149-image outdoor benchmark** (no seesaw)
- [ ] Beats it on the real failure footage through `/detect`
- [ ] Quiet on empty scenes at the deployment threshold (0.45)
- [ ] Entry written into `vision/MODEL_REGISTRY.md` — **currently stale at v13; v14 and v15
      are missing**
