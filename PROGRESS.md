# Progress log – e-MTB routing map

_Last updated: 2026-11-17_

## ▶ Resume here

**Scoring + style done (2026-11-17)** → next: step 4, check the first Colli Euganei map in BaseCamp.

**BaseCamp test 1 (2026-11-17):** route "Yes Parking → Unpaved Road" climbed *30 all'ora* (way 146466291: mtb:scale 2, uphill 2, ridden only downhill by 2 OSM traces, name not in OSM) instead of the user's line (Via Sant'Eusebio → track 34993038 → Via Scalette → Via Roverello, 3 rides). Cause: shortcut 3.8 km vs 6.2 km and speed 3 vs 5 isn't enough. Fix: whole-trail "only ridden downhill" rule (≥ 2 traces + technical → uphill one-way; ≥ 5 any; 1 trace → soft points). `tools/route_sim.py` (Dijkstra on the scored OSM file with Garmin speed classes) reproduced BaseCamp's bad route exactly with the old scores, and gives the user's line with the new ones. Map rebuilt.

**BaseCamp test 2 (2026-11-17, profile Mountain Biking = right one, matches the Edge):** avoided 30 all'ora but climbed untagged paths 946418500/499 (~200 m at 23–31 %, no tags, no traffic) instead of gate → Via Roverello.
**Key finding:** BaseCamp bike routing behaves as if every road is capped at ~20 km/h. `route_sim.py` reproduces both BaseCamp routes only with that cap (4.35 vs 4.31 km). So road_speed 1..7 are equivalent; only speed 0 (4x cost), oneway and no-access matter. The Edge may also use road_class (couch T2).
Fixes: sustained-wall uphill block (200 m > 30 %, 500 m > 22 %); walls checked on raw gradient even on rough ground (bug: rough used to skip the wall block); strong penalties (>= 2 points) → speed 0; speed <= 2 → class 0. Sim now gives the user's line. User's climbs still 98 % open.

**BaseCamp test 3 (2026-11-17): user says everything looks right**; several extra routes they tried look plausible. Step 4 passed for Colli Euganei.
Tools: `tools/check_routes.py` (BaseCamp GPX export vs scores and rides), `tools/route_sim.py`.

Build Colli from scratch (after segments/match): `python -m gsn.score --area colli_euganei && python -m gsn.terrain --area colli_euganei && python -m gsn.mkmap --area colli_euganei --install-basecamp`
→ `out/colli_euganei/GSN_colli_euganei.img` (2.7 MB) and BaseCamp map *e-MTB Nord-Est colli_euganei*.

**Edge 530 couch test: DONE (2026-11-17), all 3 pass.** Next: step 2 of the to-do list (scoring).

| Test | Edge 530 result |
|---|---|
| TEST 1 one-way | ✅ same route as BaseCamp |
| TEST 2 low priority | ✅ avoids the T2 road. Detour differs: BaseCamp takes the bike bridge (way 93314882, `highway=path`+`bicycle=designated` → cycleway, road_speed 1), the Edge goes ~600 m east via Via Camin (tertiary, road_speed 3 / class 1) |
| TEST 3 no bikes | ✅ same route as BaseCamp |

**Lesson:** the Edge weights road_speed/road_class more than BaseCamp does and will take a longer way on faster roads. BaseCamp is a good check for blocks/penalties, not an exact copy of the Edge's route choice. In the real style, speeds of good bike infrastructure and forestry tracks must be high enough (not 0–1), or the Edge will drag routes onto roads.

Clean-up on the Edge: turn the Garmin maps back on in Map Information, or delete `GSN_couch_test_v2.img`.

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
1. ~~Finish the Edge couch test~~ done, all pass.
2. ~~**Scoring**~~ done: `python -m gsn.score --area X` → `out/X/X_gsn.osm.pbf` (ways split where the score changes; tags `gsn:speed`, `gsn:class`, `gsn:oneway`, `gsn:bike=no`, debug `gsn:pts`/`gsn:why`). Check with `python tools/validate_score.py --area X my_rides/*.zip`.
   Colli result: your trail climbs 98 % routable, 93 % at speed ≥ 3. Blocked: 59 km no-bikes (bicycle=no/dismount, access=no, steps), 44 km uphill-only blocks (mtb:scale:uphill ≥ 4, walls, rough+steep).
   Lessons: OSM `smoothness=very_bad` means "MTB ok" (Colli mappers use very_bad/horrible on easy paths), so smoothness counts only when there's no mtb:scale tag; you climb `mtb:scale:uphill=2` (mild penalty only); 50 m DEM walls are noisy (wall only > 40 %); YAML `no` must be quoted.
3. ~~**Real mkgmap style**~~ done: `style/gsn/` = mkgmap default + `gsn:*` rules on top (speed/class pinned with `mkgmap:road-speed-min`=`-max`, confirmed in mkgmap's ConvertedWay; `gsn:oneway`; `gsn:bike=no/yes`). `gsn/terrain.py`: 1" hgt tiles for hill shading + 20 m contours (100 m major) from smoothed TINITALY. `gsn/mkmap.py`: osmium sort/merge → splitter → mkgmap (gmapsupp + gmapi).
   - bicycle=dismount is now a strong soft penalty (speed 0, class 0, not lifted by traces), not a block (user, 2026-11-17).
   - Default mkgmap access keeps bikes off footway/bridleway: trail-like ones (sac_scale/mtb:scale/unpaved, no bicycle tag) get `gsn:bike=yes`; urban footways stay pedestrian.
   - Still to do: **TYP file** for readable trail rendering (colour by score?) — needs a check that custom line types don't change Edge routing. Rendering is mkgmap default for now.
   - mkgmap's StyleTester crashes in r4924 (NullPointerException), so style checks go through BaseCamp.
4. ~~**First Colli Euganei map**~~ BaseCamp checks passed (2026-11-17) after 2 fix rounds. Edge on-device test of `GSN_colli_euganei.img` still to do when convenient.
5. ~~**Bulk route checker**~~ `tools/bulk_check.py --area X -n 400`: random trail-to-trail routes routed like BaseCamp (`route_sim`, 20 km/h cap; matches 3 user BaseCamp routes at 82–100 % shared length). Result after fixes (2026-11-17, Colli): detour median 1.05x, p90 1.2x; steep beyond your max+2 ≈ 3 % of routes; no-bike pieces 0. Open: uphill on non-technical downhill-only trails (n ≥ 2) 14 %, uphill on tech ≥ 3 at speed 0 8 %, no route because of our blocks 6 %.
   Fixes from it: sustained gradients measured along "strokes" (ways chained where they continue straight, < 45°), so climbs split into many ways count whole; sustained limits 200 m [20,24,27], 500 m [15,17.5,20]; rough+steep at 100 m > 18 %; uphill-3 stays soft (blocking it made 9 % of destinations unreachable). Your climbs: 1.1 % blocked, 5.6 % speed 0.
6. **Montello**: process traces, second test area.
7. **Paganella DH check**: never route uphill on the DH lines (needs a Trentino extract plus Trentino LiDAR/TINITALY tiles).
8. **Trailforks coexistence (user priority, 2026-11-17: Trailforks trailheads are the purpose of the project).**
   Research: the Edge 530 has an integrated Trailforks basemap (trails + parking/trailhead POIs) that IS routable with the Edge's built-in routing; Trailforks' own downloadable Garmin basemap is NOT routable. "MTB Trail Navigation" lists nearby Trailforks trails (unlocked by logging in). Nothing official says which routable map wins when two overlap; Garmin forum advice is "one routable map per area".
   Device checks asked from the user: (a) list of maps in Map Information; (b) does MTB Trail Navigation still list trails/trailheads and Go with the Trailforks map DISABLED; (c) if not, a couch test near home on a Trailforks trail blocked in our map, with Trailforks on vs off.
   Results (2026-11-17): (b) NO, MTB Trail Navigation needs the Trailforks map enabled. Trailforks API: not pursued (personal projects not approved).
   The Trailforks map on the Edge only covers official Trailforks areas: with only Trailforks on, nothing is drawn at Vigonza. So the couch test at home can't tell which map wins.
   Maps on the Edge (read from the TRE headers, 2026-11-17): gmapprom2.img = Trailforks 2019.10, TRANSPARENT overlay (flag 0x02), draw priority 31 (the max);
   gmapprom.img / gmapprom1.img = Garmin Cycle Map EU NE / SW 2019.10, priority 30; ours = 25 (mkgmap default). No official doc on which routable map wins when maps overlap.
   Built area padova_colli (Vigonza -> Colli, traces reuse colli_euganei) with --draw-priority=31 (map.draw_priority) and hiking/MTB route refs as trail labels ("Sent N").
   GSN_padova_colli.img copied to the Edge 2026-11-17. Test from home: `python tools/edge_test.py --area padova_colli` -> out/padova_colli/edge_test/
   (TEST V -> Bluline trailhead 45.29790,11.70845; TEST C -> Monte Venda 'Castigamatti' point 45.31580,11.69329, to confirm). Wrong (no-rules) lines climb 1.1-1.2 km against our blocks; ours 0.
   Edge result (both maps on): TEST V PASS (Via Cengolina to the trail, no trap). TEST C: Edge goes Torreglia > Via Roccolo; legal on our map if it stays on Via Roccolo to Via Sottovenda (test_C_roccolo.png), fail if it leaves Via Roccolo onto trails west of the Roccolo bend. Pending user check.
   TEST C on the Edge (both maps): Torreglia > Via Roccolo > Sent 22 down to Calto Freddo > legal speed-0 path up to Via Venda (no blocks) = PASS so far.
   Last 100 m: the star sits at the fence corner of the ex military base on Monte Venda; on our map every way to it is uphill-blocked (Castigamatti DH) or no-bikes (Via Venda service access=no, fence).
   The Edge went round the base through the fence to reach it. With Trailforks OFF it does the same -> Garmin itself uses no-bike/closed ways for the last stretch when the destination is only reachable through them.
   Photo of the Edge: it reached the star by looping south through the fence. The rider's usual line (Sent 2/4 to the crossroads, right on 146719922, right on 146719944, 56 m on 1351005023) was blocked as "sustained wall":
   the 500 m window ran into the 33-43 % wall straight ahead (45768463) that riders never take. FIX in gsn/score.py add_sustained: sustained 200/500/1000 m capped by the piece's own worst 100 m,
   plus wall100 (100 m windows centred on the piece, along strokes) so walls split across short ways are still caught. Colli: 9.1 km of sustained blocks removed (6.4 km of it < 20 % itself);
   ridden climbs blocked 0.25 -> 0.07 km, speed 0 5.6 -> 3.5 %. Test C now ends on the rider's line. Map rebuilt + copied to the Edge.
   Test C re-run on the Edge with the fixed map: PASS (Sent 2/4 > right > right > trailhead, no fence).
   Phone app idea (user): offline map + tap a trailhead + send to the Edge. Plan: hand-off from the Trailforks app ('Directions' -> our app, a single point), offline routing on our scored data, course to the Edge via USB-OTG into Garmin/NewFiles. Pending: Android or iPhone.
   CONCLUSION (2026-11-17): Trailforks overlay does not override our routing in tests V and C -> keep both maps enabled (Trailforks for trail names + MTB Trail Navigation).
   Possible fix if it matters: make access=no / bicycle=no ways non-routable (drawn only), so Garmin can't use them even near the destination.
   From home the routes approach from the north (Galzignano / Pirio), not via Valsanzibio cemetery or Via Cengolina: those exact starts need an on-site test.
   The on-device "who wins" test (Trailforks only vs both on) needs a start inside a Trailforks area (Colli), or our map extended to cover home -> Colli.
9. **Scale up** to Trentino + Veneto; hotspot trace downloads (Garda, Paganella, Val di Sole, Lessinia, Grappa, Asiago, Bassano).
10. **`build.py`**: one command for everything, plus a README with build and install steps.
11. Later: local rules research (on hold), all of Italy plus the Alps.

## Open questions
- **(User idea, revisit at the end)** Prefer a doable shorter route over a very much longer "perfect" one. Penalties must not push detours to absurd lengths. Possible levers: cap how low a penalised-but-rideable segment can go, check detour ratios in the bulk route checker, and choose road_class so long routes still use trails.
- ~~Does the Edge 530 respect one-way, low priority and no-bikes?~~ Yes (2026-11-17).
- Does the Edge route on Trailforks trails when that map is enabled?
- Is it worth using the anonymous (timestamp-less) OSM points as a "some traffic here" hint? About 1M points in Colli Euganei.
