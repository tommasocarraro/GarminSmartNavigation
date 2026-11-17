"""Edge 'who wins' test: does the Edge route on our map when the Trailforks overlay is also enabled?

From home, route to a few Colli trailheads on the scored map twice:
  expected = our map, as Garmin should route it (tools/route_sim.py, bike cap)
  wrong    = same roads with our scores and uphill blocks removed (plain shortest bike-legal):
             roughly what a map without our rules does.
A test only discriminates when the wrong route climbs one of the 'trap' ways and ours doesn't.

Writes out/<area>/edge_test/: edge_test.gpx (expected + wrong tracks, destination waypoints)
and one PNG per test.

Usage: python tools/edge_test.py --area padova_colli
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import matplotlib
import numpy as np
import osmium
import yaml
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
import route_sim as R  # noqa: E402
from gsn.dem import Dem  # noqa: E402

HOME = (45.4292, 11.9607)     # Busa di Vigonza (couch test centre)
TESTS = [
    # name, destination, trap ways (original OSM ids) the route must NOT climb, note
    ("V", "Valsanzibio -> blue-line trailhead", (45.29790, 11.70845), [146466291],
     "fail if it climbs 30 all'ora"),
    ("C", "Via Cengolina -> Castigamatti (Monte Venda)", (45.31580, 11.69329), [],
     "compare with your ridden line"),
]


def graph(ways, plain):
    idx, coords, edge = {}, [], {}
    for wid, nodes, t in ways:
        if plain:
            if t.get("bicycle") == "no" or t.get("highway") == "steps":
                continue
            ow = {"yes": "yes", "1": "yes", "true": "yes", "-1": "-1"}.get(t.get("oneway"))
            kmh = 1.0
        else:
            ow = t.get("gsn:oneway") or {"yes": "yes", "1": "yes", "true": "yes", "-1": "-1"}.get(t.get("oneway"))
            kmh = R.KMH[int(t["gsn:speed"])]
        for (r1, la1, lo1), (r2, la2, lo2) in zip(nodes[:-1], nodes[1:]):
            for r, la, lo in ((r1, la1, lo1), (r2, la2, lo2)):
                if r not in idx:
                    idx[r] = len(coords)
                    coords.append((la, lo, r))
            L = math.hypot((la2 - la1) * 111000, (lo2 - lo1) * 78000)
            gow = t.get("gsn:oneway")
            for u, v, d in ((idx[r1], idx[r2], 1), (idx[r2], idx[r1], -1)):
                if (ow == "yes" and d < 0) or (ow == "-1" and d > 0):
                    continue
                c = L / kmh
                against = (gow == "yes" and d < 0) or (gow == "-1" and d > 0)
                if (u, v) not in edge or edge[(u, v)][1] > c:
                    edge[(u, v)] = (wid, c, L, t, against)
    keys = list(edge)
    M = csr_matrix(([edge[k][1] for k in keys], ([k[0] for k in keys], [k[1] for k in keys])),
                   shape=(len(coords), len(coords)))
    return M, np.array(coords), edge


def shortest(G, A, B):
    M, coords, edge = G
    def near(p):
        return int(np.argmin(np.hypot((coords[:, 0] - p[0]) * 111000, (coords[:, 1] - p[1]) * 78000)))
    a = near(A)
    D, P = dijkstra(M, directed=True, indices=a, return_predecessors=True)
    # like Garmin: snap the destination to the nearest point that can be reached (within 300 m)
    dist = np.hypot((coords[:, 0] - B[0]) * 111000, (coords[:, 1] - B[1]) * 78000)
    dist[~np.isfinite(D)] = np.inf
    b = int(np.argmin(dist))
    if not np.isfinite(dist[b]) or dist[b] > 300:
        return None
    nodes, v = [b], b
    while v != a:
        v = P[v]
        nodes.append(v)
    return nodes[::-1]


def trap_nodes(src_pbf, way_ids):
    out = {}
    want = set(way_ids)
    for o in osmium.FileProcessor(str(src_pbf)):
        if o.is_way() and o.id in want:
            out[o.id] = [n.ref for n in o.nodes]
    return out


def trap_use(G, nodes, traps, dem):
    """Metres climbed along each trap way (uphill direction only)."""
    coords = G[1]
    res = {}
    for wid, refs in traps.items():
        rs = set(refs)
        up = L = 0.0
        for u, v in zip(nodes[:-1], nodes[1:]):
            if int(coords[u][2]) in rs and int(coords[v][2]) in rs:
                z = dem.sample(np.array([coords[u][0], coords[v][0]]), np.array([coords[u][1], coords[v][1]]))
                L += G[2][(u, v)][2]
                up += max(0.0, z[1] - z[0])
        res[wid] = (L, up)
    return res


def summary(G, nodes):
    edge = G[2]
    es = [edge[(u, v)] for u, v in zip(nodes[:-1], nodes[1:])]
    km = sum(e[2] for e in es) / 1000
    trail = sum(e[2] for e in es if e[3].get("highway") in ("path", "track", "footway", "bridleway")) / 1000
    names, last = [], None
    for e in es:
        n = e[3].get("name") or ("(" + e[3].get("highway", "?") + ")")
        if n != last:
            names.append(n)
            last = n
    blocked = sum(e[2] for e in es if e[4])
    slow = sum(e[2] for e in es if e[3].get("gsn:speed") == "0" and e[3].get("highway") in TRAILISH)
    return km, trail, names, blocked, slow


TRAILISH = ("path", "track", "footway", "bridleway")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", required=True)
    a = ap.parse_args()
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    dem = Dem(Path("cache/dem") / cfg["sources"]["dem"], cfg["areas"][a.area]["bbox"])
    ways = R.load(Path("out") / a.area / f"{a.area}_gsn.osm.pbf")
    ours, plain = graph(ways, False), graph(ways, True)
    traps = trap_nodes(Path("cache/areas") / f"{a.area}.osm.pbf", [w for t in TESTS for w in t[3]])
    out = Path("out") / a.area / "edge_test"
    out.mkdir(parents=True, exist_ok=True)
    gpx = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<gpx version="1.1" creator="gsn edge_test" xmlns="http://www.topografix.com/GPX/1/1">']
    for tid, name, dest, trap_ids, note in TESTS:
        gpx.append(f'<wpt lat="{dest[0]}" lon="{dest[1]}"><name>TEST {tid}</name><desc>{name}</desc></wpt>')
        print(f"\n=== TEST {tid}: home -> {name}  ({note})")
        res = {}
        for label, G in (("expected", ours), ("wrong", plain)):
            nodes = shortest(G, HOME, dest)
            if nodes is None:
                print(f"  {label}: no route")
                continue
            km, trail, names, blocked, slow = summary(G, nodes)
            tu = trap_use(G, nodes, {w: traps[w] for w in trap_ids if w in traps}, dem)
            res[label] = (G, nodes)
            # the last part (inside the hills) is what matters: print the last 12 names
            print(f"  {label:8s} {km:5.1f} km ({trail:.1f} km trails); against our uphill blocks {blocked:.0f} m, "
                  f"speed-0 trail {slow:.0f} m; traps: "
                  + (", ".join(f"way {w}: {L:.0f} m, +{up:.0f} m climbed" for w, (L, up) in tu.items()) or "-"))
            print("           ... " + " > ".join(names[-12:]))
            pts = G[1][nodes]
            gpx.append(f"<trk><name>TEST {tid} {label}</name><trkseg>"
                       + "".join(f'<trkpt lat="{p[0]:.7f}" lon="{p[1]:.7f}"/>' for p in pts)
                       + "</trkseg></trk>")
        # map of the hill part (within 3 km of the destination)
        fig, ax = plt.subplots(figsize=(8, 8))
        lat0, lon0 = dest
        for wid, nodes_, t in ways:
            arr = np.array([(la, lo) for _, la, lo in nodes_])
            if np.min(np.hypot((arr[:, 0] - lat0) * 111, (arr[:, 1] - lon0) * 78)) > 3.5:
                continue
            hw = t.get("highway")
            trailish = hw in ("path", "track", "footway", "bridleway")
            ax.plot(arr[:, 1], arr[:, 0], color="#999" if trailish else "#555",
                    lw=0.6 if trailish else 1.2, ls="--" if trailish else "-", zorder=1)
        for w, refs in traps.items():
            if w in trap_ids:
                pts = [(la, lo) for wid, nodes_, t in ways for r, la, lo in nodes_ if r in set(refs)]
                if pts:
                    p = np.array(pts)
                    ax.scatter(p[:, 1], p[:, 0], s=6, color="#d32f2f", zorder=2, label="trap: 30 all'ora")
        for label, col, lw, ls in (("wrong", "#d32f2f", 2, ":"), ("expected", "#1565c0", 3, "-")):
            if label in res:
                G, nodes = res[label]
                p = G[1][nodes]
                ax.plot(p[:, 1], p[:, 0], color=col, lw=lw, ls=ls, zorder=3,
                        label="expected (our map)" if label == "expected" else "WRONG (no rules)")
        ax.plot(dest[1], dest[0], "*", color="#7b1fa2", ms=18, zorder=4, label=f"TEST {tid} destination")
        ax.set_xlim(lon0 - 3.0 / 78, lon0 + 3.0 / 78)
        ax.set_ylim(lat0 - 3.0 / 111, lat0 + 3.0 / 111)
        ax.set_aspect(1 / math.cos(math.radians(lat0)))
        ax.set_title(f"TEST {tid}: {name}\n{note}")
        h, lab = ax.get_legend_handles_labels()
        seen = dict(zip(lab, h))
        ax.legend(seen.values(), seen.keys(), loc="lower left", fontsize=8)
        fig.tight_layout()
        fig.savefig(out / f"test_{tid}.png", dpi=110)
        plt.close(fig)
    gpx.append("</gpx>")
    (out / "edge_test.gpx").write_text("\n".join(gpx))
    print(f"\nwrote {out}/edge_test.gpx and test_*.png")


if __name__ == "__main__":
    main()
