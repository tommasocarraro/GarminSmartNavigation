# Project: e-MTB-friendly offline routing map for Garmin Edge 530

## Goal
Build a custom routable Garmin map (`gmapsupp.img`) for **Trentino + Veneto** so that the Edge 530's **built-in, offline** "pick a point on the map → Go" navigation gives sensible routes for an **e-MTB**. No phone or connectivity on the trail. Garmin firmware can't be modified, so all the routing logic goes into the map data. The device's own router then uses it.

Main problem to fix: Garmin routes you up downhill/DH trails, rocky technical paths, steps, closed or overgrown trails, and absurd walls. Steep climbs on decent surfaces are fine (it's an e-bike). The map must route **anywhere**, including unknown trails. Known trails are only for testing.

Target device: Edge 530 (it may also be used on a friend's Edge MTB, so keep it compatible).

## Inputs
- **OSM:** Geofabrik `italy/nord-est` extract, clipped to the Trentino and Veneto boundaries (osmium).
- **Elevation:** Copernicus GLO-30. Evaluate TINITALY 10 m as a better option for Italy.
- **Other riders' GPS traces:** OSM public GPS traces via the API (`/api/0.6/trackpoints?bbox=…&page=N`; max 0.25 deg² per bbox, paged). Be rate-limit friendly: cache everything, allow resuming. Start with MTB hotspots: Garda/Riva-Arco, Paganella, Val di Sole, Lessinia, Colli Euganei, Monte Grappa, Asiago, Bassano. Expand later.
- **Sample rides:** I'll provide `.FIT` files (from a friend's Edge MTB). Use them **only for calibration and validation**, not to restrict routing to known trails. Colli Euganei is the main test area with known trails.

## Pipeline
1. Download, clip, cache.
2. Split ways into short segments where gradient changes meaningfully (or about 50–100 m). Compute per segment **in both directions**: average gradient, worst 50 m, and profile bumpiness (roughness).
3. Process GPS traces: keep only trackable/identifiable traces with timestamps; filter out hikers by speed (bikes are much faster on flats and descents); **map-match** to OSM ways; count **independent traces per segment per direction**, plus median uphill speed. Each trace counts once per segment.
4. Score each segment/direction (see below) → write custom tags → modified OSM file.
5. Custom **mkgmap** style that maps the custom tags to `road_speed` (0–7), `road_class` (0–4) and `oneway`. Use splitter if needed. Output `gmapsupp.img`, optionally with contours and DEM shading.
6. Document how to install it on the Edge 530.

## Scoring rules (defaults, confirm with me first)
Use **soft penalties** (low road_speed) wherever possible, so steep or hard trails stay usable when nothing else works. **Hard blocks** (one-way in the downhill direction, or no access) only for:
- uphill on DH trails: `mtb:type=downhill`, bike-park trails, high `mtb:scale` (≥3?), or traces going only downhill with enough confidence
- `highway=steps`, `bicycle=no`/`dismount`, `access=no`, `sac_scale` ≥ T4 (`demanding_alpine_hiking`)
- uphill that is both rocky and steep: `surface=rock/scree` or bad `smoothness` + steep
- extreme walls: the threshold comes from calibration

**Priority of signals (highest first):**
1. **Other riders' traffic,** weighted by confidence (number of independent traces, e.g. weight n/(n+k)):
   - ridden uphill by others → strongly reduce the gradient/technical penalty (locals climb it, so it works)
   - only ridden downhill → evidence for an uphill block
   - no traffic at all → mild penalty (possibly overgrown or abandoned)
2. **OSM MTB route relations** (`route=mtb`) → bonus.
3. **OSM tags:**
   - penalize: high `mtb:scale`, `mtb:scale:uphill`, `smoothness` very_bad or worse, `surface=rock/scree`, `trail_visibility` bad/horrible, `informal=yes`, `abandoned:*`/`disused:*`
   - prefer: `highway=track` with `tracktype` grade1–3 (forestry roads are the natural e-bike climbs)
4. **Terrain:** gradient matters mainly **when combined with roughness**. Steep forestry road = fine; steep rocky path = bad.

**Calibration from the sample rides (50 m windows, while riding):** typical climbs 8–12%, 90th percentile 12–20%, max 29–31% at about 6 km/h. Proposed: no gradient penalty up to ~20%, mild 20–25%, strong over 25% sustained, unrideable only for short walls over ~32–35% or rough + steep. Re-derive these from the FIT files and confirm with me.

**Known limitation:** besides one-way, Garmin routing can't treat the two directions of a segment differently, so penalties apply both ways. That's acceptable for getting to trailheads.

## Local rules
Research the **current Trentino rules on bicycles on mountain paths** (SAT trails, width rules, any provincial law) and any relevant Veneto or park rules. Block trails where the OSM data makes it possible to identify them. Tell me what you found and the sources.

## How to work
- Start by asking me for: my e-bike limits (steepest comfortable climb; how much rock/technical terrain I accept uphill) and whether to include contours.
- Build and test on a **small bbox (Colli Euganei)** first, then scale to Trentino + Veneto.
- Check trace coverage early and report how much the traffic signal will actually contribute.
- Validate routing on a desktop if possible (Garmin BaseCamp uses the same `.img` routing), comparing with my known trails and the FIT rides, before I test on the Edge.
- Make the whole thing re-runnable with one command (for fresh OSM data or new areas, later all of Italy plus the Alps). Keep thresholds in a config file.
- Deliverables: scripts, mkgmap style, config, README (build + install steps), `gmapsupp.img`.

## Installing on the Edge 530 (to verify and document)
Copy the `.img` into `Garmin/` on the device → Map Information: enable the custom map, disable Garmin Cycle Map → routing mode Mountain Biking → turn popularity routing off while testing. Check that one-way restrictions are respected by bike routing.
