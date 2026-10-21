# Drone model checklist — ~20 models per visual family

Companion to `list-of-drones-grouped-by-size-and-shape.md`. That file explains *why* the
seven families exist; this one is the **collection target list** — roughly 20 named models
per family to scrape and label.

> **Scope note.** This is for the **general detector** (the v9→v15 line), NOT the DJI Neo
> track. `dji-neo/` v2 is deliberately a one-room, one-airframe model and should not be fed
> this data. Keep the two efforts separate.

**Read the "distinct silhouettes" count in each section before treating 20 as a target.**
Many models inside a family are visually near-identical — a Mini 3 and a Mini 4 Pro differ
by details a detector at 100m will never resolve. The number that matters for training is
how many genuinely different *shapes* you cover, not how many product names.

---

## 1. Folding camera quads (sub-250g to ~1kg)

Four arms that fold against the body, gimbal camera slung under the nose. The default
consumer shape and the bulk of real traffic.

| # | Model | Note |
|---|---|---|
| 1 | DJI Mavic 4 Pro | current flagship |
| 2 | DJI Mavic 3 Pro | triple camera bulge |
| 3 | DJI Mavic 3 / 3 Classic | |
| 4 | DJI Mavic 2 Pro | legacy, still common |
| 5 | DJI Mavic 2 Zoom | |
| 6 | DJI Air 3S | |
| 7 | DJI Air 3 | dual camera |
| 8 | DJI Air 2S | |
| 9 | DJI Mavic Air 2 | |
| 10 | DJI Mini 5 Pro | |
| 11 | DJI Mini 4 Pro | |
| 12 | DJI Mini 4K | |
| 13 | DJI Mini 3 / 3 Pro | |
| 14 | DJI Mini 2 / 2 SE | |
| 15 | DJI Mavic Mini | original sub-250g |
| 16 | DJI Phantom 4 Pro / V2 / RTK | **rigid white body, does NOT fold — different silhouette** |
| 17 | Autel EVO II Pro V3 | orange body |
| 18 | Autel EVO Nano+ / Lite+ | discontinued 2025, still flying |
| 19 | Potensic Atom 2 / Atom | |
| 20 | Holy Stone HS720 / HS175D / HS600D | budget tier |
| 21 | Ruko F11 · SkyRover X1 | budget tier |
| 22 | Parrot Anafi / Anafi Ai | unusual forward-canted arms |

**Distinct silhouettes: ~5.** Folded-arm quad (most of the list), Phantom rigid body,
Parrot's canted arms, tri-camera nose bulge, bare sub-250g. Collect breadth for scale and
livery variation, not because each name is a new shape.

---

## 2. Palm-sized selfie drones and enclosed-prop micros

Fits in a hand. Often fully caged propellers. Smallest pixel footprint of anything here —
this is the recall problem, not the false-alarm problem.

| # | Model | Note |
|---|---|---|
| 1 | DJI Neo | 135g, fully ducted |
| 2 | DJI Neo 2 | |
| 3 | DJI Flip | folds flat, full guards |
| 4 | HoverAir X1 | |
| 5 | HoverAir X1 Pro | |
| 6 | HoverAir X1 Pro Max | |
| 7 | HoverAir X1 Promax | |
| 8 | Skydio 2 / 2+ | legacy consumer only |
| 9 | Zero Zero Hover Camera / Hover 2 | HoverAir's predecessor |
| 10 | Snaptain A15 / S5C | |
| 11 | Ryze/DJI Tello | education staple, very common |
| 12 | Ryze Tello EDU | |
| 13 | Potensic A20 / A20W | |
| 14 | Eachine E58 / E88 | |
| 15 | Holy Stone HS210 / HS420 | |
| 16 | Selfly Camera | phone-case drone |
| 17 | AirSelfie 2 | |
| 18 | ZeroTech Dobby | |
| 19 | Hubsan Zino Mini Pro | |
| 20 | SIMREX X300C | |

**Distinct silhouettes: ~3.** Fully caged rectangle (Neo/Flip), open micro quad
(Tello/Eachine), folding micro. Volume here should go into *scale and distance* variation
rather than more model names.

---

## 3. Ducted FPV and cinewhoops

Propellers enclosed in circular ducts. The duct rings dominate the outline and make these
read completely differently from an open quad.

| # | Model | Note |
|---|---|---|
| 1 | DJI Avata | |
| 2 | DJI Avata 2 | |
| 3 | DJI Avata 360 | 8K 360°, omnidirectional sensing |
| 4 | DJI FPV | partially exposed, transitional shape |
| 5 | DJI Neo (caged mode) | overlaps family 2 |
| 6 | Insta360 Antigravity A1 | 360° competitor |
| 7 | BetaFPV Pavo25 / Pavo20 | |
| 8 | BetaFPV Pavo Pico | |
| 9 | BetaFPV Pavo360 | |
| 10 | iFlight ProTek25 / ProTek35 | |
| 11 | iFlight BumbleBee | |
| 12 | GEPRC Cinelog25 / Cinelog35 | |
| 13 | GEPRC Cinebot30 | |
| 14 | Flywoo FlyLens / CineRace20 | |
| 15 | HGLRC Veyron | |
| 16 | Diatone Taycan MX-C | |
| 17 | EMAX Babyhawk II HD | |
| 18 | Happymodel Mobula7 / Mobula8 | tiny whoop |
| 19 | Axisflying Manta | |
| 20 | NewBeeDrone AcroBee | tiny whoop |
| 21 | Sub250 Nanofly | |

**Distinct silhouettes: ~4.** Full cinewhoop with four ducts, tiny whoop (very small, all
duct), 360° spherical camera on top, DJI FPV's semi-open frame.

---

## 4. Open-frame FPV (DIY and kit builds)

⚠️ **This is a shape, not a product line.** Carbon X-frame, exposed motors, no shell, a
battery strapped on top and a camera tilted up front. Almost no body mass — at distance
it's thin arms and a battery brick. The most relevant family on the counter-drone side and
the one your current data covers worst.

| # | Model | Note |
|---|---|---|
| 1 | iFlight Nazgul5 V3 | the most-cloned 5" |
| 2 | iFlight Nazgul Evoque F5 | |
| 3 | iFlight XL10 | 10" long range |
| 4 | iFlight Chimera7 / Chimera9 | long range |
| 5 | GEPRC Mark5 / Mark5 HD | |
| 6 | GEPRC Crocodile Baby / Crocodile5 | |
| 7 | GEPRC Smart35 | |
| 8 | TBS Source One V5 | open-source frame |
| 9 | TBS Ethix Mr Steele | |
| 10 | ImpulseRC Apex / Reverb | |
| 11 | Armattan Marmotte / Badger | |
| 12 | Five33 Switchback | |
| 13 | Rotor Riot CL1 | |
| 14 | EMAX Hawk Apex / Hawk 5 | |
| 15 | Diatone Roma F5 / Roma L5 | |
| 16 | SpeedyBee Master 5 V2 | |
| 17 | HGLRC Sector X5 | |
| 18 | Flywoo Explorer LR | long range 4" |
| 19 | Foxeer Aura / Reaper | |
| 20 | Custom Betaflight/ELRS builds | **the majority of real units** |

**Distinct silhouettes: ~4** by size class — 3", 5" freestyle, 7" long range, 10". Within a
size class they are functionally identical, so scrape by *size and viewing angle*, not by
brand. Prioritise images against sky at distance, which is the actual detection case.

---

## 5. Enterprise inspection and public-safety quads

Larger, rigid, with a prominent sensor payload slung underneath — the payload ball can read
as a second object to a detector.

| # | Model | Note |
|---|---|---|
| 1 | DJI Matrice 400 | current flagship, LiDAR + mmWave |
| 2 | DJI Matrice 350 RTK | |
| 3 | DJI Matrice 300 RTK | legacy, huge installed base |
| 4 | DJI Matrice 30 / 30T | folding enterprise |
| 5 | DJI Matrice 4E / 4T | |
| 6 | DJI Matrice 4D / 4TD | dock variants |
| 7 | DJI Dock 3 (docked aircraft) | |
| 8 | DJI Mavic 3E / 3T / 3M | Mavic body + RTK module on top |
| 9 | DJI Matrice 200 / 210 | legacy |
| 10 | Autel EVO Max 4T / 4T XE | |
| 11 | Autel EVO II Dual 640T V3 | |
| 12 | Skydio X10 / X10D | dominant US non-DJI |
| 13 | Skydio X2 / X2D | |
| 14 | Skydio R10 | |
| 15 | Parrot Anafi USA / USA Gov / UKR | |
| 16 | BRINC Lemur 2 | indoor tactical, glass-breaker |
| 17 | BRINC Responder | |
| 18 | Teal Black Widow | |
| 19 | Inspired Flight IF800 Tomcat | NDAA heavy payload |
| 20 | Inspired Flight IF1200A | |
| 21 | Freefly Astro | |
| 22 | Anzu Robotics Raptor / Raptor T | |
| 23 | Flyability Elios 3 | **fully caged sphere — unique shape** |

**Distinct silhouettes: ~6.** Large rigid quad with gimbal, folding enterprise quad,
Skydio's fixed-arm profile, caged sphere (Elios), docked-on-pad, Mavic-with-beacon.

---

## 6. Heavy-lift hex and octo (agriculture, cargo, cinema)

Six or eight arms, very large, usually seen close and low. This is the old v9 close-range
failure mode — large objects filling much of the frame.

| # | Model | Note |
|---|---|---|
| 1 | DJI Agras T100 | |
| 2 | DJI Agras T70P | |
| 3 | DJI Agras T55 | |
| 4 | DJI Agras T50 | |
| 5 | DJI Agras T40 | |
| 6 | DJI Agras T30 | |
| 7 | DJI Agras T25 / T25P | |
| 8 | DJI Agras T20 / T16 | legacy, common in field |
| 9 | XAG P100 Pro | |
| 10 | XAG P150 | |
| 11 | XAG V40 | |
| 12 | DJI FlyCart 30 | cargo |
| 13 | DJI FlyCart 100 | ~150kg MTOW |
| 14 | DJI Inspire 3 | cinema, transforming arms |
| 15 | DJI Inspire 2 | |
| 16 | Freefly Alta X | |
| 17 | Freefly Alta 8 | |
| 18 | Harris Aerial Carrier H6 / H4 | |
| 19 | Hylio AG-116 / AG-272 | |
| 20 | Acecore Zoe / Noa | |
| 21 | Griff Aviation Griff 135 | |
| 22 | Volocopter VoloDrone | very large |

**Distinct silhouettes: ~4.** Spray hexa with tanks and booms, cargo box-carrier, cinema
octo with top-mounted or under-slung camera, transforming-arm Inspire.

---

## 7. Fixed-wing and VTOL

⚠️ **Read the class-definition warning before collecting this family.** Your model is
currently taught that plane-shaped means *not a drone* — the Marked-Null airplane confusers
in `dji-neo-dataset` are correct and deliberate. An eBee or a Zipline sits squarely in that
space. Adding these as positives while those confusers remain as negatives pulls training in
two directions at once. **Settle the class definition before scraping this family.**

| # | Model | Note |
|---|---|---|
| 1 | WingtraOne Gen II | tailsitter — takes off nose-up |
| 2 | WingtraOne Gen III | |
| 3 | AgEagle / senseFly eBee X | foam flying wing, belly-lands |
| 4 | senseFly eBee Geo / eBee TAC | |
| 5 | Quantum Systems Trinity Pro | quadplane |
| 6 | Quantum Systems Trinity F90+ | |
| 7 | Quantum Systems Vector | |
| 8 | JOUAV CW-15 | |
| 9 | JOUAV CW-25E | |
| 10 | JOUAV CW-007 | |
| 11 | Autel Dragonfish Standard / Pro | |
| 12 | DeltaQuad Pro / Evo | |
| 13 | Zipline P1 | fixed-wing delivery, catapult launch |
| 14 | Zipline P2 | droid-on-tether delivery |
| 15 | Wing (Alphabet) delivery aircraft | |
| 16 | Matternet M2 | |
| 17 | Manna delivery drone | |
| 18 | Censys Sentaero BVLOS | |
| 19 | Applied Aeronautics Albatross | |
| 20 | UAV Factory Penguin C | |
| 21 | AeroVironment Puma / Raven | military-derived, hand-launched |
| 22 | Moneypro MVT series | |

**Distinct silhouettes: ~5.** Flying wing (no tail), conventional wing-and-tail, tailsitter
in vertical vs horizontal attitude, quadplane with lift rotors on booms, delivery aircraft
in hover vs cruise. **Capture VTOL types in both attitudes** — a quadplane hovering looks
nothing like the same aircraft in forward flight.

---

## Does ~20 models per family cover it?

**Model count is the wrong unit.** Twenty DJI folding quads add barely more shape
information than five, while family 4 has almost no product names yet the widest real-world
variation. What to aim for instead:

| axis | target |
|---|---|
| **distinct silhouettes per family** | see each section — 3 to 6, roughly 30 across all seven |
| **images per family, post-dedupe** | 800–1,500 |
| **viewing angles per silhouette** | below, side, three-quarter, head-on, against sky AND against buildings |
| **scale** | close (fills frame) through distant (a few pixels) — distance matters more than model |
| **lighting** | daylight, overcast, dusk, backlit against bright sky |

Two things beat adding model names:

1. **Distance coverage.** A Mini 4 Pro at 150m and the same drone at 10m are further apart
   for a detector than a Mini 4 Pro and a Mini 3 at the same distance.
2. **Background variety.** Sky, treeline, rooftops, building facades, indoor ceilings. Your
   v14→v15 failure was entirely a background-domain problem, not a model-coverage problem.

Families 1, 2 and 5 are already well covered by existing data. **Spend the effort on 4, 6
and 7** — and on 7 only after the class-definition question is answered.

## Using this with the scraper

`drone_families_dataset.py` already carries a query catalog keyed to these same seven
families (`f1_folding_quad` … `f7_fixed_wing_vtol`). To target a specific model, add its
name to that family's `queries` list — the browser engine returns ~150 originals per query,
so each added model name is worth roughly 150 candidate images before dedupe.

```bash
python drone_families_dataset.py report                    # current coverage
python drone_families_dataset.py browser --families gap    # f3, f4, f6, f7
python drone_families_dataset.py filter  --families gap --keep-rejects
python drone_families_dataset.py dedupe  --families all --src raw/filtered
```

Model names and current lineups shift; verify against manufacturer sites before treating any
row here as authoritative.
