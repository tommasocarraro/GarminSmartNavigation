"""Approximate Garmin's bike routing on the scored OSM file (the exact input mkgmap gets).

Cost of a way = length / speed(gsn:speed) with Garmin's road_speed classes. Respects
gsn:oneway, the original OSM oneway, gsn:bike / bicycle=no and mkgmap's default
"no bikes on footway/pedestrian/steps". NOT Garmin's real algorithm (unknown):
only a check that the scores push routes the right way.

Usage: python tools/route_sim.py --area colli_euganei --from LAT LON --to LAT LON
       python tools/route_sim.py --area colli_euganei --gpx out/colli_euganei/basecamp_routes.gpx
"""
from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

import numpy as np
import osmium
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

CAR_KMH = [5, 20, 40, 60, 80, 90, 110, 128]   # mkgmap road_speed -> km/h
BIKE_CAP = 20      # BaseCamp bike routing behaves as if every road were capped at ~20 km/h
KMH = [min(v, BIKE_CAP) for v in CAR_KMH]
FOOT_ONLY = {"footway", "pedestrian", "steps"}
BIKE_YES = {"yes", "designated", "permissive", "official"}


def bike_allowed(t):
    if t.get("gsn:bike") == "no" or t.get("bicycle") == "no":
        return False
    if t.get("gsn:bike") == "yes" or t.get("bicycle") in BIKE_YES:
        return True
    hw = t.get("highway")
    if hw in FOOT_ONLY or hw in ("motorway", "motorway_link"):
        return False
    if t.get("access") in ("no", "private"):
        return False
    return True


def load(pbf: Path):
    loc, ways = {}, []
    for o in osmium.FileProcessor(str(pbf)).with_locations():
        if o.is_way() and "gsn:speed" in o.tags:
            t = dict(o.tags)
            if not bike_allowed(t):
                continue
            try:
                nodes = [(n.ref, n.lat, n.lon) for n in o.nodes]
            except osmium.InvalidLocationError:
                continue
            ways.append((o.id, nodes, t))
    return ways


def build(ways):
    idx, coords = {}, []
    edge = {}
    for wid, nodes, t in ways:
        kmh = KMH[int(t["gsn:speed"])]
        ow = t.get("gsn:oneway") or {"yes": "yes", "1": "yes", "true": "yes", "-1": "-1"}.get(t.get("oneway"))
        for (r1, la1, lo1), (r2, la2, lo2) in zip(nodes[:-1], nodes[1:]):
            for r, la, lo in ((r1, la1, lo1), (r2, la2, lo2)):
                if r not in idx:
                    idx[r] = len(coords)
                    coords.append((la, lo))
            L = math.hypot((la2 - la1) * 111000, (lo2 - lo1) * 78000)
            c = L / 1000 / kmh
            for u, v, d in ((idx[r1], idx[r2], 1), (idx[r2], idx[r1], -1)):
                if (ow == "yes" and d < 0) or (ow == "-1" and d > 0):
                    continue
                if (u, v) not in edge or edge[(u, v)][1] > c:
                    edge[(u, v)] = (wid, c, L, t)
    keys = list(edge)
    M = csr_matrix(([edge[k][1] for k in keys], ([k[0] for k in keys], [k[1] for k in keys])),
                   shape=(len(coords), len(coords)))
    return M, np.array(coords), edge


def route(M, coords, edge, A, B):
    def near(p):
        return int(np.argmin(np.hypot((coords[:, 0] - p[0]) * 111000, (coords[:, 1] - p[1]) * 78000)))
    a, b = near(A), near(B)
    D, P = dijkstra(M, directed=True, indices=[a], return_predecessors=True)
    if not np.isfinite(D[0][b]):
        return None
    path, v = [], b
    while v != a:
        u = P[0][v]
        path.append(edge[(u, v)])
        v = u
    return path[::-1]


def describe(path):
    out = []
    for wid, c, L, t in path:
        if out and out[-1][0] == wid:
            out[-1][2] += L
        else:
            out.append([wid, t.get("highway"), L, t.get("gsn:speed"), t.get("name", ""), t.get("gsn:why", "")])
    lines = [f"  {sum(o[2] for o in out) / 1000:.2f} km, {sum(p[1] for p in path) * 60:.1f} min (Garmin speed classes, capped)"]
    for wid, hw, L, sp, nm, why in out:
        lines.append(f"   way {wid:>13} {hw:12s} {L:5.0f} m spd {sp} {nm[:22]:22s} {why[:50]}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", required=True)
    ap.add_argument("--from", dest="A", nargs=2, type=float)
    ap.add_argument("--to", dest="B", nargs=2, type=float)
    ap.add_argument("--gpx", type=Path, help="use start/end of each route in this BaseCamp export")
    ap.add_argument("--cap", type=float, default=BIKE_CAP, help="bike speed cap km/h (0 = car speeds)")
    a = ap.parse_args()
    global KMH
    KMH = [min(v, a.cap) if a.cap else v for v in CAR_KMH]
    M, coords, edge = build(load(Path("out") / a.area / f"{a.area}_gsn.osm.pbf"))
    pairs = []
    if a.gpx:
        for rte in re.findall(r"<rte>(.*?)</rte>", a.gpx.read_text(), re.S):
            pts = [(float(x), float(y)) for x, y in re.findall(r'<rtept lat="([-\d.]+)" lon="([-\d.]+)"', rte)]
            if len(pts) >= 2 and pts[0] != pts[-1]:
                pairs.append((re.search(r"<name>(.*?)</name>", rte).group(1), pts[0], pts[-1]))
    if a.A and a.B:
        pairs.append(("cli", tuple(a.A), tuple(a.B)))
    for name, A, B in pairs:
        path = route(M, coords, edge, A, B)
        print(f"\n=== {name}  {A} -> {B}")
        print(describe(path) if path else "  no route")


if __name__ == "__main__":
    main()
