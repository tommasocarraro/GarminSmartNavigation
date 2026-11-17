# Progress log – e-MTB routing map

_Last updated: 2026-11-17_

## ▶ Resume here tomorrow

**Edge 530 couch test (Busa di Vigonza): finish it and report the results.**

Map on the Edge: `GSN_couch_test_v2.img` in `GARMIN/Garmin/`. The old `GSN_couch_test.img` was deleted.
Turn it on in *Settings → Activity Profiles → MTB → Navigation → Map → Map Information*, and turn the other maps off.
Routing: Mountain Biking, Popularity Routing off, Avoidances off.

Pick each destination with **Navigation → Browse Map**, put the cursor on it, then **Go**:

| Test | Destination | Pass if | Status |
|---|---|---|---|
| TEST 1 one-way | **middle** T1 mark on Via Argine Sinistro (NE of Busa) | the route reaches it from the **east** end | calculated, not yet compared |
| TEST 2 low priority | **TEST 2 point** ~1 km **south** of the T2 marks on Nona Strada, near Via Panà, at the bottom edge of the map. It isn't drawn on the Edge, so place the cursor by hand using BaseCamp. *Don't* navigate to a T2 mark. | the route does **not** run along the T2 marks | not done |
| TEST 3 no bikes | **TEST 3 point** just **west** of the T3 marks on Via Serenissima. *Don't* navigate to a T3 mark. | the route does **not** run along the T3 marks | calculated, not yet compared |

Take a phone photo of each route. Then:
- If TEST 2 is too fiddly, ask for a **v3** with the TEST 2 point moved further inside the map.
- Afterwards: turn the Garmin maps back on in Map Information, or delete the test `.img`.

Reference pictures: `out/couch_test/FOR_EDGE/expected_routes.png`, and BaseCamp → Maps menu (top menu bar) → *GSN Couch Test v2*.

---

## Decisions made with the user
- **Climbing limits are learned from the FIT rides**, not chosen by hand. The user finds percentages hard to reason about and asked me to decide.
- **Uphill rock/roots:** light only (about `mtb:scale` 1).
- **Finale Ligure rides:** calibration only, Liguria is not in the map area.
- **Paganella ride (24930631650):** lift-assisted DH. Excluded from climb stats; used as a "never route uphill here" test.
- **Map look:** contours (20 m, thicker every 100 m) **and** hill shading. "Fancy and readable", file size doesn't matter.
- **Trailforks map must stay usable** on the Edge in the final setup (see the open questions).
- **Local rules** (bike bans on paths): on hold until the user asks.
- **Validation:** BaseCamp on the Mac (the user runs it), plus my own route checker. The user lives far from the MTB areas.
- **Workflow goal:** one command builds any area, without needing Claude, including other riders' traces.

## Done

### Setup
- Conda env **`gsn`**: Python 3.11, osmium, pyosmium, rasterio, scipy, OpenJDK 17 (`~/miniconda3/envs/gsn/lib/jvm/bin/java`).
- `vendor/`: mkgmap r4924, splitter r654.
- `cache/osm/nord-est-latest.osm.pbf` (Geofabrik). Clipped areas in `cache/areas/` (colli_euganei, montello, busa_test).
- DEM tiles: `cache/dem/tinitaly/` (w50070, w50570) and `cache/dem/glo30/` (N45 E011/E012).

### Calibration from the rides (`tools/calibrate.py` → `out/calibration/calibration.json`)
9 rides, 163 km of climbing (Paganella excluded):

| window | median | p90 | p99 | max ridden |
|---|---|---|---|---|
| 50 m | 7.5 % | 13.4 % | 20.6 % | 31 % at 5–6 km/h (33–37 % only at walking pace) |
| 100 m | 7.5 % | 12.9 % | 19.7 % | 28.7 % |
| 200 m | 7.4 % | 12.2 % | 18.0 % | 24.5 % |
| 500 m | 6.7 % | 11.2 % | 16.3 % | 17.5 % |
| 1 km | 6.3 % | 10.2 % | 15.0 % | 15.5 % |

The thresholds are in `config.yaml` → `gradient:`. No penalty up to roughly the rider's own maximums. One-way downhill only for walls clearly above anything ridden (50 m > 36 %, 100 m > 32 %), unless traces show the wall is climbed.

### DEM choice (`tools/dem_vs_baro.py` → `out/calibration/dem_vs_baro.json`)
**TINITALY 10 m** beats GLO-30, which sees tree canopy. Correlation with the barometer: 50 m 0.66 vs 0.59, 200 m 0.92 vs 0.88. Over ≥ 200 m, DEM gradient ≈ barometric gradient, so the limits carry over directly. 50 m is noisy, so 100 m is the main wall window.

### Phase 0: do routing controls work in Garmin? (`tools/couch_test.py`, `tools/check_basecamp.py`)
Test map around Busa di Vigonza. BaseCamp results (Cycling, Faster Time), **test map vs control map**:
- one-way: control rides against it; test map ✅ respects it
- low priority (`mkgmap:road-speed-max=0`, `road-class-max=0`): control uses 100 %; test map ✅ uses 0 %, taking a detour only 4–6 % longer
- `bicycle=no`: control uses 100 %; test map ✅ uses 0 %

**Lessons**
- mkgmap's default style already gives `highway=path` road_speed 0, so the real style must redefine trail speeds.
- `base-style` is deprecated and buggy. Copy the default style and prepend our rules.
- BaseCamp (Mac) needs the `--gmapi` bundle in `~/Library/Application Support/Garmin/Maps`. Its map list is in the macOS top menu bar → Maps.
- The Edge doesn't draw a non-routable 0x01 line or the 0x2c04 attraction POI near the map edge, but does draw 0x6400 points. Always check visuals **on the device**.
- The Edge 530 Navigation menu only has Browse Map, Courses, MTB Trail Navigation and Saved Locations (no coordinate entry).

### Other riders' GPS traces
- `gsn/trace_download.py`: cached, resumable, rate-limited OSM trackpoint pager. Colli Euganei: 282 pages. **Montello: done (75 pages)**, not processed yet.
- `gsn/traces.py`: parse, clean, classify by speed on DEM flats/descents (config `traces:`).
  Colli Euganei: 331k timestamped points → **79 bike traces (1,544 km)**, 60 hike, 34 motor dropped.
- `gsn/segments.py`: ways split at junctions and into ≤ 100 m pieces, with gradient (avg, worst 50/100 m, both directions) and roughness.
  Colli Euganei: 38,884 segments, 1,025 km of trails.
- `gsn/match.py`: snaps traces to segments, counts independent traces per segment and direction, plus speed.
  Validated on the user's rides (`tools/validate_match.py`): **89–93 % of each ride matched, direction agrees with the barometer 92 %.**
- **Coverage:** 22 % of trails have ≥ 1 bike trace; only 10 % of trail climbs ridden uphill. **But 55 % of the user's own trail climbs were also climbed by OSM riders** (72 % have some traffic). Downhill-only evidence is about 0.
  → Uphill traffic is a strong bonus, no traffic only a mild penalty. DH protection must come from tags and gradient.

## To do (in order)
1. **Finish the Edge couch test** (above). If the Edge behaves differently from BaseCamp, rethink how the blocks work.
2. **Scoring** (`gsn/score.py`): tags + gradient × roughness + traffic + `route=mtb` → `road_speed`/`road_class`/oneway/blocks per segment. Write the modified OSM file with split ways.
3. **Real mkgmap style** (`style/gsn/`): redefine trail speeds, contours, hill shading (DEM), readable trail rendering (TYP file?). Check it on the Edge.
4. **First Colli Euganei map** → BaseCamp checks: the user's climbs should be routable, steps and steep rocky paths avoided.
5. **Bulk route checker** (my own Dijkstra on the same costs): random routes, flag steps, DH-uphill and walls.
6. **Montello**: process traces, second test area.
7. **Paganella DH check**: never route uphill on the DH lines (needs a Trentino extract plus Trentino LiDAR/TINITALY tiles).
8. **Trailforks coexistence test**: with Trailforks and our map both on, does the Edge route on Trailforks trails and bypass our penalties?
9. **Scale up** to Trentino + Veneto; hotspot trace downloads (Garda, Paganella, Val di Sole, Lessinia, Grappa, Asiago, Bassano).
10. **`build.py`**: one command for everything, plus a README with build and install steps.
11. Later: local rules research (on hold), all of Italy plus the Alps.

## Open questions
- Does the Edge 530 respect one-way, low priority and no-bikes like BaseCamp does? (Pending.)
- Does the Edge route on Trailforks trails when that map is enabled?
- Is it worth using the anonymous (timestamp-less) OSM points as a "some traffic here" hint? About 1M points in Colli Euganei.
