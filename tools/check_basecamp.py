"""Check BaseCamp-calculated routes (GPX export) against the couch-test roads.

Usage: python tools/check_basecamp.py out/couch_test/FOR_EDGE/couch_test_points_results.GPX
"""
from __future__ import annotations

import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import osmium

NS = {"g": "http://www.topografix.com/GPX/1/1", "x": "http://www.garmin.com/xmlschemas/GpxExtensions/v3"}
NEAR_M = 10.0


def to_xy(lat, lon, lat0):
    k = math.cos(math.radians(lat0)) * 111320.0
    return np.c_[(np.asarray(lon) * k), (np.asarray(lat) * 110540.0)]


def read_routes(gpx: Path):
    root = ET.parse(gpx).getroot()
    out = {}
    for rte in root.findall("g:rte", NS):
        name = rte.find("g:name", NS).text
        pts = []
        for rp in rte.findall("g:rtept", NS):
            pts.append((float(rp.get("lat")), float(rp.get("lon"))))
            for s in rp.iter(f"{{{NS['x']}}}rpt"):
                pts.append((float(s.get("lat")), float(s.get("lon"))))
        out[name] = pts
    return out


def way_geoms(pbf: Path, ids: set[int]):
    g = {}
    for o in osmium.FileProcessor(str(pbf)).with_locations():
        if o.is_way() and o.id in ids:
            g[o.id] = [(n.lat, n.lon) for n in o.nodes]
    return g


def project(P, line):
    """For each point in P: (distance to polyline, position along polyline in m)."""
    seg_a, seg_b = line[:-1], line[1:]
    d = seg_b - seg_a
    L = np.hypot(d[:, 0], d[:, 1])
    cum = np.r_[0, np.cumsum(L)]
    res = []
    for p in P:
        t = np.clip(((p - seg_a) * d).sum(1) / np.maximum(L**2, 1e-9), 0, 1)
        proj = seg_a + d * t[:, None]
        dist = np.hypot(*(p - proj).T)
        i = int(dist.argmin())
        res.append((float(dist[i]), float(cum[i] + t[i] * L[i])))
    return np.array(res), float(cum[-1])


def densify(P, step=3.0):
    out = [P[0]]
    for a, b in zip(P[:-1], P[1:]):
        n = max(1, int(np.hypot(*(b - a)) / step))
        for k in range(1, n + 1):
            out.append(a + (b - a) * k / n)
    return np.array(out)


def main():
    gpx = Path(sys.argv[1])
    tests = json.loads(Path("out/couch_test/tests.json").read_text())
    geoms = way_geoms(Path("out/couch_test/busa_test.osm.pbf"), {t["way"] for t in tests})
    routes = read_routes(gpx)
    lat0 = 45.43
    verdicts = []
    for t in tests:
        name = next((n for n in routes if re.search(rf"TEST {t['id']}\b", n)), None)
        if not name:
            verdicts.append((t["id"], "MISSING", "route not found in GPX"))
            continue
        R = densify(to_xy(*np.array(routes[name]).T, lat0))
        W = to_xy(*np.array(geoms[t["way"]]).T, lat0)
        pr, wlen = project(R, W)
        on = pr[:, 0] < NEAR_M
        covered = 0.0
        if on.any():
            pos = pr[on, 1]
            covered = float(pos.max() - pos.min())
        if t["kind"] == "oneway":
            # route must travel along the way in the direction of the OSM node order (oneway=yes)
            pos = pr[on, 1]
            if len(pos) < 3:
                v = ("FAIL?", "route never reaches the one-way road")
            else:
                trend = float(np.median(np.diff(pos)))
                ok = trend > 0
                v = ("PASS" if ok else "FAIL",
                     f"travels {'with' if ok else 'AGAINST'} the one-way direction over {covered:.0f} m of {wlen:.0f} m")
        else:
            frac = covered / wlen
            ok = frac < 0.2
            v = ("PASS" if ok else "FAIL", f"uses {covered:.0f} m of the {wlen:.0f} m test road ({frac:.0%})")
        verdicts.append((t["id"], *v))
        print(f"TEST {t['id']} ({t['kind']}): {v[0]} - {v[1]}")
    Path(gpx.with_suffix(".verdict.json")).write_text(json.dumps(verdicts, indent=1))


if __name__ == "__main__":
    main()
