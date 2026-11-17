"""Bulk route checker: many random routes on the scored map, routed like BaseCamp (tools/route_sim.py),
then checked for things the map must never do and for absurd detours.

Per route (as ridden, from origin to destination):
  - no-bike pieces used (steps, bicycle=no) and dismount pieces
  - uphill on technical trails (mtb:scale or mtb:scale:uphill >= 3) or on downhill-only trails
  - steepest uphill over 100 / 200 / 500 m measured on the DEM along the route itself,
    compared with the rider's maxima (config rider.max_ridden)
  - detour = route length / shortest length allowed for bikes (ignoring our scores)
Writes a summary, the worst cases, and a GPX of flagged routes to open in BaseCamp.

Usage: python tools/bulk_check.py --area colli_euganei [-n 400] [--seed 1]
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import yaml
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
import route_sim as R  # noqa: E402
from gsn.dem import Dem  # noqa: E402
from gsn.segments import hav  # noqa: E402

TRAILS = {"track", "path", "footway", "bridleway", "cycleway"}


def plain_graph(ways):
    """Same bike access rules, original oneways only, cost = length: the 'shortest legal' baseline."""
    idx, coords, edge = {}, [], {}
    for wid, nodes, t in ways:
        if t.get("bicycle") == "no" or t.get("highway") == "steps":
            continue
        ow = {"yes": "yes", "1": "yes", "true": "yes", "-1": "-1"}.get(t.get("oneway"))
        for (r1, la1, lo1), (r2, la2, lo2) in zip(nodes[:-1], nodes[1:]):
            for r, la, lo in ((r1, la1, lo1), (r2, la2, lo2)):
                if r not in idx:
                    idx[r] = len(coords)
                    coords.append((la, lo))
            L = math.hypot((la2 - la1) * 111000, (lo2 - lo1) * 78000)
            for u, v, d in ((idx[r1], idx[r2], 1), (idx[r2], idx[r1], -1)):
                if (ow == "yes" and d < 0) or (ow == "-1" and d > 0):
                    continue
                if (u, v) not in edge or edge[(u, v)] > L:
                    edge[(u, v)] = L
    keys = list(edge)
    M = csr_matrix(([edge[k] for k in keys], ([k[0] for k in keys], [k[1] for k in keys])),
                   shape=(len(coords), len(coords)))
    return M, idx


def path_nodes(P, a, b):
    out, v = [b], b
    while v != a:
        v = P[v]
        if v < 0:
            return None
        out.append(v)
    return out[::-1]


def worst_uphill(lat, lon, z, win):
    d = np.r_[0, np.cumsum(hav(lat[:-1], lon[:-1], lat[1:], lon[1:]))]
    if d[-1] < win:
        return 0.0
    s = np.arange(0, d[-1] - win, 5.0)
    zi = np.interp(d, d, z)
    g = (np.interp(s + win, d, zi) - np.interp(s, d, zi)) / win * 100
    return float(g.max()) if len(g) else 0.0


def steepest_window(lat, lon, z, win):
    """Sample index range [i, j) of the steepest uphill window."""
    d = np.r_[0, np.cumsum(hav(lat[:-1], lon[:-1], lat[1:], lon[1:]))]
    best, bi = -1e9, (0, 1)
    j = 0
    for i in range(len(d)):
        while j < len(d) - 1 and d[j] - d[i] < win:
            j += 1
        if d[j] - d[i] < win:
            break
        g = (z[j] - z[i]) / win
        if g > best:
            best, bi = g, (i, j)
    return bi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", required=True)
    ap.add_argument("-n", type=int, default=400)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--min-km", type=float, default=1.5)
    ap.add_argument("--max-km", type=float, default=7.0)
    a = ap.parse_args()
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    bbox = cfg["areas"][a.area]["bbox"]
    maxr = cfg["rider"]["max_ridden"]
    dem = Dem(Path("cache/dem") / cfg["sources"]["dem"], bbox)
    ways = R.load(Path("out") / a.area / f"{a.area}_gsn.osm.pbf")
    M, coords, edge = R.build(ways)
    Mp, idx_p = plain_graph(ways)
    # route_sim and plain graph number nodes the same way only if built identically: map by coordinate
    key_p = {}
    for wid, nodes, t in ways:
        for r, la, lo in nodes:
            if r in idx_p:
                key_p[(round(la, 7), round(lo, 7))] = idx_p[r]

    # candidate endpoints: trail nodes in the hills (DEM > 60 m)
    rng = np.random.default_rng(a.seed)
    cand = set()
    for wid, nodes, t in ways:
        if t.get("highway") in TRAILS:
            cand.update((round(la, 7), round(lo, 7)) for _, la, lo in nodes[:: max(1, len(nodes) // 3)])
    cand = np.array(sorted(cand))
    z = dem.sample(cand[:, 0], cand[:, 1])
    cand = cand[np.nan_to_num(z) > 60]
    cidx = {tuple(np.round(c, 7)): i for i, c in enumerate(coords)}
    pairs = []
    while len(pairs) < a.n:
        i, j = rng.integers(len(cand), size=2)
        km = hav(cand[i, 0], cand[i, 1], cand[j, 0], cand[j, 1]) / 1000
        if a.min_km <= km <= a.max_km:
            pairs.append((tuple(cand[i]), tuple(cand[j])))

    flags, rows = Counter(), []
    rows_noroute = []
    det = []
    for A, B in pairs:
        ia, ib = cidx.get(tuple(np.round(A, 7))), cidx.get(tuple(np.round(B, 7)))
        pa, pb = key_p.get(tuple(np.round(A, 7))), key_p.get(tuple(np.round(B, 7)))
        if None in (ia, ib, pa, pb):
            continue
        D, P = dijkstra(M, directed=True, indices=ia, return_predecessors=True)
        nodes = path_nodes(P, ia, ib)
        Dp = dijkstra(Mp, directed=True, indices=pa)[pb]
        if not np.isfinite(Dp):
            flags["(no legal route at all: isolated endpoint)"] += 1
            continue
        if nodes is None:
            flags["NO ROUTE because of our blocks"] += 1
            rows_noroute.append((A, B))
            continue
        pts = coords[nodes]
        es = [edge[(u, v)] for u, v in zip(nodes[:-1], nodes[1:])]
        L = sum(e[2] for e in es)
        # resample every 5 m for gradients
        d = np.r_[0, np.cumsum(hav(pts[:-1, 0], pts[:-1, 1], pts[1:, 0], pts[1:, 1]))]
        s = np.arange(0, d[-1], 5.0)
        la, lo = np.interp(s, d, pts[:, 0]), np.interp(s, d, pts[:, 1])
        zz = dem.sample(la, lo)
        zz = np.where(np.isnan(zz), np.nanmean(zz), zz)
        f = []
        tags = [e[3] for e in es]
        dz = [dem.sample(np.array([coords[u][0], coords[v][0]]), np.array([coords[u][1], coords[v][1]]))
              for u, v in zip(nodes[:-1], nodes[1:])]
        up = [(b - a_) / max(e[2], 1) * 100 for (a_, b), e in zip(dz, es)]
        if any(t.get("gsn:bike") == "no" or t.get("highway") == "steps" for t in tags):
            f.append("no-bike piece")
        if any(t.get("bicycle") == "dismount" for t in tags):
            f.append("dismount")
        tech_up = sum(e[2] for e, t, g in zip(es, tags, up) if g > 4 and (
            float(str(t.get("mtb:scale:uphill", t.get("mtb:scale", "0"))).rstrip("+-") or 0) >= 3))
        if tech_up > 30:
            f.append("uphill tech>=3")
        def dh_n(t):
            w_ = t.get("gsn:why", "")
            i = w_.find("downhill-only")
            return int(w_[i + 13]) if i >= 0 and w_[i + 13:i + 14].isdigit() else 0
        dh_up = sum(e[2] for e, t, g in zip(es, tags, up) if g > 4 and dh_n(t) >= 2)
        if dh_up > 50:
            f.append("uphill downhill-only n>=2")
        dh1 = sum(e[2] for e, t, g in zip(es, tags, up) if g > 4 and dh_n(t) == 1)
        if dh1 > 50:
            f.append("(info) uphill downhill-only n=1")
        w = {W: worst_uphill(la, lo, zz, W) for W in (100, 200, 500)}
        # ground type along the route, per 5 m sample
        cum_e = np.r_[0, np.cumsum([e[2] for e in es])]
        scale = d[-1] / max(cum_e[-1], 1)
        e_at = np.clip(np.searchsorted(cum_e * scale, s, side="right") - 1, 0, len(es) - 1)
        for W in (100, 200, 500):
            if w[W] > maxr[W] + 2:      # 2 points of margin: DEM vs barometer, and within your own maxima
                st = steepest_window(la, lo, zz, W)
                hw = Counter(tags[e_at[k]].get("highway") for k in range(st[0], st[1]))
                paved = sum(v for k_, v in hw.items() if k_ not in TRAILS) / max(sum(hw.values()), 1)
                ways_in = len({es[e_at[k]][0] for k in range(st[0], st[1])})
                kind = "road" if paved > 0.5 else "trail"
                f.append(f"steeper than ridden+2 {W}m on {kind}{' (multi-way)' if ways_in > 1 else ''}")
        ratio = L / max(Dp, 1)
        det.append(ratio)
        if ratio > 1.8:
            f.append("detour>1.8x")
        trail = sum(e[2] for e, t in zip(es, tags) if t.get("highway") in TRAILS) / max(L, 1)
        for x in f:
            flags[x] += 1
        rows.append(dict(A=A, B=B, km=L / 1000, ratio=ratio, w=w, trail=trail, flags=f, pts=pts))

    n = len(rows)
    print(f"{n} routes ({a.min_km}-{a.max_km} km apart, trail endpoints in the hills)")
    print(f"  median length {np.median([r['km'] for r in rows]):.1f} km, trail share median "
          f"{np.median([r['trail'] for r in rows]):.0%}")
    det = np.array(det)
    print(f"  detour vs shortest bike-legal: median {np.median(det):.2f}x, p90 {np.percentile(det, 90):.2f}x, "
          f"max {det.max():.2f}x")
    print("Flags (routes):")
    for k, v in flags.most_common():
        print(f"  {k:28s} {v:4d}  ({v / max(n, 1):.0%})")
    bad = sorted([r for r in rows if any(not x.startswith("(") for x in r["flags"])], key=lambda r: -len(r["flags"]))
    print("\nWorst cases:")
    for r in bad[:15]:
        print(f"  {r['A'][0]:.5f},{r['A'][1]:.5f} -> {r['B'][0]:.5f},{r['B'][1]:.5f} {r['km']:.1f} km x{r['ratio']:.2f} "
              f"w100 {r['w'][100]:.0f}% w200 {r['w'][200]:.0f}% w500 {r['w'][500]:.0f}%  {', '.join(r['flags'])}")
    if rows_noroute:
        print("\nNo route because of our blocks (first 10):")
        for A, B in rows_noroute[:10]:
            print(f"  {A[0]:.5f},{A[1]:.5f} -> {B[0]:.5f},{B[1]:.5f}")
    out = Path("out") / a.area / "bulk_flagged.gpx"
    with open(out, "w") as fh:
        fh.write('<?xml version="1.0"?>\n<gpx version="1.1" creator="gsn bulk_check" '
                 'xmlns="http://www.topografix.com/GPX/1/1">\n')
        for k, r in enumerate(bad[:50]):
            fh.write(f"<trk><name>B{k:02d} {', '.join(r['flags'])}</name><trkseg>\n")
            for la_, lo_ in r["pts"]:
                fh.write(f'<trkpt lat="{la_:.7f}" lon="{lo_:.7f}"/>\n')
            fh.write("</trkseg></trk>\n")
        fh.write("</gpx>\n")
    print(f"\nflagged routes for BaseCamp: {out}")


if __name__ == "__main__":
    main()
