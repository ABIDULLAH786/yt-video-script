Here's the most complete list I can build, grouped by visual family rather than by brand. For detection, shape and size matter more than who makes the drone, so this doubles as a coverage checklist for our dataset. DJI holds over 70% of the global consumer and commercial market, so most of what flies near any site will be DJI. Older models matter too, because people keep flying them for years after launch.

**1. Folding camera quads (sub-250g to about 1kg): most of what you'll see in the sky**

- **Current DJI models.** The current consumer lineup includes the Mavic 4 Pro, Air 3S, Mini 5 Pro, Neo 2, Lito 1, Lito X1, Flip, Avata 360, and Inspire 3, alongside the Mini 4 Pro and Air 3.
- **Older DJI models still widely flown.** Mini 4K, Mini 3 and Mini 3 Pro, Mini 2 and Mini 2 SE, Mavic Mini, Air 2S, Mavic Air 2, Mavic 3 / 3 Classic / 3 Pro, Mavic 2 Pro and Zoom.
- **The Phantom 4 series.** The Pro, V2 and RTK are no longer sold, but the installed base is huge. The white rigid body gives it a different silhouette from the folding models.
- **Potensic.** The Atom 2 sells for about $330, weighs under 249g, and competes directly with the Mini 4 Pro. The original Atom is also common.
- **Autel.** EVO II Pro V3 is the main one. The EVO Nano+ and Lite+ lines were discontinued in July 2025, but plenty are still flying.
- **Budget brands.** Holy Stone (HS720, HS175D, HS600D), Ruko F11, SkyRover X1. Potensic and Holy Stone together outsell any other single DJI competitor, though at lower price points.
- **Parrot.** Anafi and Anafi AI.

**2. Palm-sized selfie drones and enclosed-prop micros**

- **DJI.** Neo, Neo 2, Flip, and possibly Lito 1 and X1. I don't have a confirmed form factor for the Lito models yet.
- **HoverAir.** X1, X1 Pro, X1 Pro Max.
- **Skydio 2 and 2+.** These are legacy units only; Skydio left the consumer market in August 2023 and now sells only to government and enterprise.

**3. Ducted FPV and cinewhoops**

- **DJI.** Avata, Avata 2, and the older DJI FPV. The Avata 360 shoots 8K 360° video and has integrated prop guards and omnidirectional sensing.
- **Insta360 Antigravity A1.** This is the 360° drone the Avata 360 was built to compete with.

**4. Open-frame FPV (DIY and kit builds)**

- **Form factor:** 3" to 10" freestyle and racing quads such as iFlight Nazgul, GEPRC, and BetaFPV whoops, plus countless Betaflight/ELRS DIY builds.
- **No dominant model.** This is a shape, not a product. It's also the most relevant family on the threat side of counter-drone work.

**5. Enterprise inspection and public-safety quads**

- **DJI Matrice 4E / 4T** and the dock versions. Dock 3 works with the Matrice 4D or 4TD and supports both fixed and vehicle-mounted installation.
- **DJI Matrice 400.** It's the current flagship, with 59-minute flight time, up to 6 kg payload, and built-in rotating LiDAR and mmWave radar.
- **Other DJI enterprise.** Matrice 350 RTK, Matrice 300 RTK (legacy), Matrice 30 / 30T, Mavic 3E, 3T, and 3M.
- **Autel.** The EVO Max 4T XE carries a 48MP zoom, a 50MP wide, and a 640×512 thermal sensor. The EVO II Dual 640T V3 is also common.
- **Skydio.** X10, X10D, R10, and Skydio Dock.
- **Parrot.** Anafi USA, Anafi USA Gov, Anafi UKR.
- **Others.** BRINC Lemur 2 and Responder, Teal Black Widow. Inspired Flight's IF800 Tomcat and IF1200A are the main heavy-payload NDAA-compliant options.

**6. Heavy-lift hex and octo: agriculture, cargo, cinema**

- **DJI Agras.** The current family is T25P, T55, T70P and T100, with the T50 and T25 still in active use. Older T40, T30, and T20 units are common in the field.
- **XAG.** P100 Pro and V40, widely used in Asian agriculture.
- **DJI FlyCart 30 and FlyCart 100.** The FlyCart 100 weighs up to about 150 kg at takeoff.
- **Cinema and heavy payload.** Freefly Alta X, Freefly Astro, DJI Inspire 3 and Inspire 2, Harris Aerial H6, Hylio.

**7. Fixed-wing and VTOL**

- **WingtraOne Gen II.** A tailsitter: it takes off pointing nose-up, then pitches forward into wing-borne flight.
- **AgEagle eBee X.** One of the most proven fixed-wing mapping platforms, with thousands deployed. It's a foam airframe that belly-lands.
- **Quantum Systems Trinity Pro and F90+.** Quadplanes, meaning a fixed wing with separate lift motors, which is now the most common VTOL design.
- **Others.** JOUAV CW-15, CW-25E, and CW-007; Autel Dragonfish; DeltaQuad; Moneypro MVT series.
- **Delivery.** Zipline P1 (fixed-wing) and P2, Wing (Alphabet), Matternet M2, Manna.

**What this means for our dataset**

Families 1–3 are well covered, since our positives are mostly DJI Mini/Mavic/Neo shapes. That matches where the market is.

The gaps are families 4, 6 and 7:
- **Open-frame FPV** has almost no body mass. At distance it's just thin arms and a battery, a very different silhouette from a folding quad.
- **Heavy-lift hex and octo** drones are large and would often appear close up, which is exactly v9's old failure mode.
- **Fixed-wing and VTOL** has a less obvious catch. This isn't a labelling issue with the dji-neo-dataset confusers; those Marked-Null airplanes are correct. But the model is being taught that plane-shaped means "not a drone." An eBee or Zipline at distance sits right in that space, so fixed-wing UAVs would likely be suppressed.

If the client needs fixed-wing coverage, that's a class-definition decision to make before v16, not something more data alone can fix.