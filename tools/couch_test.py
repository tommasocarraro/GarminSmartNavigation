"""Phase 0 'couch test': does the Edge obey our routing controls?

Builds a small map around the rider's village with three test destinations:
  TEST 1  one-way    : POI in the middle of a path that, from the village, is
                       naturally entered from the 'wrong' end. Marked one-way
                       so the router must go round and enter from the other end.
  TEST 2  low prio   : a road/path every natural route uses; we drop it to
                       road_speed 0 / road_class 0. A slightly longer detour exists.
  TEST 3  forbidden  : same idea, but bicycle=no.

Writes:
  out/couch_test/busa_test.osm.pbf  (modified OSM)
  out/couch_test/busa_control.osm.pbf (same, untouched, with the POIs only)
  out/couch_test/tests.json + sheet.png (expected routes)

Usage: python tools/couch_test.py cache/areas/busa_test.osm.pbf --center 45.4292 11.9607
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import osmium
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

BIKE_HW = {"primary", "secondary", "tertiary", "unclassified", "residential", "living_street",
           "service", "track", "path", "cycleway", "bridleway", "primary_link", "secondary_link",
           "tertiary_link", "road"}
NICE_HW = {"unclassified", "residential", "track", "path", "cycleway", "service", "tertiary"}
R = 6371000.0


def hav(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def along(coords, frac):
    """Point at fraction `frac` of the line length."""
    seg = [hav(*coords[i], *coords[i + 1]) for i in range(len(coords) - 1)]
    goal, acc = sum(seg) * frac, 0.0
    for i, L in enumerate(seg):
        if acc + L >= goal and L > 0:
            f = (goal - acc) / L
            return (coords[i][0] + f * (coords[i + 1][0] - coords[i][0]),
                    coords[i][1] + f * (coords[i + 1][1] - coords[i][1]))
        acc += L
    return coords[-1]


def bike_ok(t: dict) -> bool:
    hw = t.get("highway")
    if hw == "footway" and t.get("bicycle") in ("yes", "designated", "permissive"):
        return True
    if hw not in BIKE_HW:
        return False
    if t.get("bicycle") in ("no", "private", "dismount"):
        return False
    if t.get("access") in ("no", "private") and t.get("bicycle") not in ("yes", "designated", "permissive"):
        return False
    if t.get("area") == "yes":
        return False
    return True


def oneway_dir(t: dict) -> int:
    if t.get("oneway:bicycle") == "no" or t.get("cycleway") == "opposite":
        return 0
    if t.get("oneway") in ("yes", "true", "1") or t.get("junction") == "roundabout":
        return 1
    if t.get("oneway") == "-1":
        return -1
    return 0


class Graph:
    def __init__(self, pbf: Path):
        self.ways = {}           # wid -> dict(nodes, coords, tags, length)
        for o in osmium.FileProcessor(str(pbf)).with_locations():
            if o.is_way():
                t = dict(o.tags)
                if not bike_ok(t):
                    continue
                try:
                    coords = [(n.lat, n.lon) for n in o.nodes]
                except osmium.InvalidLocationError:
                    continue
                nodes = [n.ref for n in o.nodes]
                if len(nodes) < 2:
                    continue
                L = sum(hav(*coords[i], *coords[i + 1]) for i in range(len(coords) - 1))
                self.ways[o.id] = {"nodes": nodes, "coords": coords, "tags": t, "length": L}
        deg = {}
        for w in self.ways.values():
            for i, n in enumerate(w["nodes"]):
                inc = 2 if 0 < i < len(w["nodes"]) - 1 else 1
                deg[n] = deg.get(n, 0) + inc
        self.deg = deg
        idx, coord = {}, {}
        for w in self.ways.values():
            for n, c in zip(w["nodes"], w["coords"]):
                if n not in idx:
                    idx[n] = len(idx)
                    coord[n] = c
        self.idx, self.coord = idx, coord
        self.rev = {v: k for k, v in idx.items()}
        self.N = len(idx)

    def matrix(self, removed: set[int] = frozenset(), oneway_override: dict[int, int] | None = None):
        rows, cols, data, self.edge_way = [], [], [], {}
        for wid, w in self.ways.items():
            if wid in removed:
                continue
            d = oneway_dir(w["tags"])
            if oneway_override and wid in oneway_override:
                d = oneway_override[wid]
            for i in range(len(w["nodes"]) - 1):
                a, b = self.idx[w["nodes"][i]], self.idx[w["nodes"][i + 1]]
                L = hav(*w["coords"][i], *w["coords"][i + 1]) + 0.01
                if d >= 0:
                    rows.append(a); cols.append(b); data.append(L); self.edge_way[(a, b)] = wid
                if d <= 0:
                    rows.append(b); cols.append(a); data.append(L); self.edge_way[(b, a)] = wid
        return csr_matrix((data, (rows, cols)), shape=(self.N, self.N))

    def path_ways(self, pred_row, src, dst):
        ws, cur = set(), dst
        while cur != src and cur >= 0:
            p = pred_row[cur]
            if p < 0:
                return None
            ws.add(getattr(self, "edge_way0", self.edge_way).get((p, cur)))
            cur = p
        return ws

    def path_coords(self, pred_row, src, dst):
        pts, cur = [], dst
        while cur >= 0:
            pts.append(self.coord[self.rev[cur]])
            if cur == src:
                break
            cur = pred_row[cur]
        return pts[::-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pbf", type=Path)
    ap.add_argument("--center", nargs=2, type=float, required=True)
    ap.add_argument("--village-radius", type=float, default=600)
    ap.add_argument("--out", type=Path, default=Path("out/couch_test"))
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    G = Graph(a.pbf)
    clat, clon = a.center
    M = G.matrix()
    G.edge_way0 = dict(G.edge_way)

    # Largest connected component only
    from scipy.sparse.csgraph import connected_components
    _, lab = connected_components(M, directed=True, connection="strong")
    big = np.bincount(lab).argmax()
    in_big = lab == big

    village = [i for n, i in G.idx.items()
               if in_big[i] and hav(clat, clon, *G.coord[n]) < a.village_radius and G.deg.get(n, 0) >= 3]
    starts = list(rng.choice(village, size=min(25, len(village)), replace=False))
    D, P = dijkstra(M, directed=True, indices=starts, return_predecessors=True)
    DT = dijkstra(M.T.tocsr(), directed=True, indices=None, limit=0) if False else None  # noqa

    def far_enough(n_idx, lo=1200, hi=3500):
        c = G.coord[G.rev[n_idx]]
        return lo < hav(clat, clon, *c) < hi

    def no_other_way_near(lat, lon, own: set[int], r=45.0):
        for wid, w in G.ways.items():
            if wid in own:
                continue
            for c in w["coords"]:
                if abs(c[0] - lat) < 0.001 and abs(c[1] - lon) < 0.0014 and hav(lat, lon, *c) < r:
                    return False
        return True

    used = set()
    tests = []

    # ---- TEST 1: one-way ------------------------------------------------------
    best = None
    for wid, w in G.ways.items():
        t = w["tags"]
        if t.get("highway") not in NICE_HW or oneway_dir(t) != 0 or not (180 < w["length"] < 600):
            continue
        A, B = w["nodes"][0], w["nodes"][-1]
        if any(G.deg.get(n, 0) > 2 for n in w["nodes"][1:-1]) or A == B:
            continue
        ia, ib = G.idx[A], G.idx[B]
        if not (in_big[ia] and in_big[ib] and far_enough(ia, 800) and far_enough(ib, 800)):
            continue
        da, db = D[:, ia], D[:, ib]
        if not (np.all(np.isfinite(da)) and np.all(np.isfinite(db))):
            continue
        # natural entry from B for every start, and going round to A costs 400-2000 m extra
        extra = da - db
        if not (np.all(extra > 400) and np.all(extra < 2000)):
            continue
        mid = along(w["coords"], 0.5)
        if not no_other_way_near(*mid, {wid}, 60):
            continue
        # the far end A must be reachable WITHOUT using the test way itself
        da2 = dijkstra(G.matrix(removed={wid}), directed=True, indices=starts)[:, ia]
        G.matrix()
        extra = da2 - db
        if not (np.all(np.isfinite(extra)) and np.all(extra > 400) and np.all(extra < 2000)):
            continue
        score = float(np.min(extra))
        if best is None or score > best[0]:
            best = (score, wid, mid)
    if best:
        _, wid, mid = best
        w = G.ways[wid]
        used.add(wid)
        # allowed direction: from A (first node) towards B -> OSM oneway=yes
        tests.append({"id": 1, "kind": "oneway", "way": wid, "poi": mid, "oneway": "yes",
                      "highway": w["tags"].get("highway"), "length_m": round(w["length"]),
                      "explain": "Must enter from the far end, not the near one."})

    # ---- TEST 2/3: avoid / forbid -------------------------------------------
    targets = [i for n, i in G.idx.items() if in_big[i] and far_enough(i) and G.deg.get(n, 0) >= 3]
    targets = list(rng.choice(targets, size=min(2000, len(targets)), replace=False))
    found = {2: None, 3: None}
    for T in targets:
        common = None
        for k in range(len(starts)):
            ws = G.path_ways(P[k], starts[k], T)
            if ws is None:
                common = set()
                break
            common = ws if common is None else common & ws
        if not common:
            continue
        tlat, tlon = G.coord[G.rev[T]]
        for wid in common:
            w = G.ways.get(wid)
            if wid is None or wid in used or w["tags"].get("highway") not in NICE_HW:
                continue
            if not (100 < w["length"] < 900) or any(G.deg.get(n, 0) > 2 for n in w["nodes"][1:-1]):
                continue
            # the test way must not touch the destination
            if min(hav(tlat, tlon, *c) for c in w["coords"]) < 150:
                continue
            Mr = G.matrix(removed={wid})
            D2 = dijkstra(Mr, directed=True, indices=starts)[:, T]
            ratio = D2 / D[:, T]
            if not np.all(np.isfinite(ratio)):
                continue
            # low priority must clearly lose against the detour: long way, short detour
            if found[2] is None and w["length"] > 220 and w["tags"].get("highway") in ("residential", "unclassified", "tertiary") and np.all(ratio > 1.03) and np.all(ratio < 1.3):
                found[2] = (T, wid, float(ratio.min()), float(ratio.max()))
                used.add(wid)
                break
            if found[3] is None and np.all(ratio > 1.15) and np.all(ratio < 1.8) :
                found[3] = (T, wid, float(ratio.min()), float(ratio.max()))
                used.add(wid)
                break
        if found[2] and found[3]:
            break
    G.matrix()  # restore edge_way for original graph
    for k, kind, explain in ((2, "low_priority", "Should take the slightly longer way round."),
                             (3, "forbidden", "Must never use the marked path.")):
        if found[k]:
            T, wid, rmin, rmax = found[k]
            tests.append({"id": k, "kind": kind, "way": int(wid), "poi": G.coord[G.rev[T]],
                          "highway": G.ways[wid]["tags"].get("highway"),
                          "length_m": round(G.ways[wid]["length"]),
                          "detour_ratio": [round(rmin, 2), round(rmax, 2)], "explain": explain})

    for t in tests:
        cs = G.ways[t["way"]]["coords"]
        t["coords"] = [along(cs, f) for f in np.linspace(0, 1, max(2, int(G.ways[t["way"]]["length"] // 60) + 1))]
    a.out.mkdir(parents=True, exist_ok=True)
    write_osm(a.pbf, a.out / "busa_test.osm.pbf", tests, modify=True)
    write_osm(a.pbf, a.out / "busa_control.osm.pbf", tests, modify=False)
    (a.out / "tests.json").write_text(json.dumps(tests, indent=1))
    draw_sheet(G, tests, (clat, clon), a.out / "sheet.png")
    print(json.dumps(tests, indent=1))


LABEL = {"oneway": "TEST1 ONE-WAY", "low_priority": "TEST2 LOW PRIORITY", "forbidden": "TEST3 NO BIKES"}


def write_osm(src: Path, dst: Path, tests: list[dict], modify: bool):
    by_way = {t["way"]: t for t in tests}
    if dst.exists():
        dst.unlink()
    w = osmium.SimpleWriter(str(dst))
    pois_written = False
    for o in osmium.FileProcessor(str(src)):
        if o.is_node():
            w.add_node(o)
            continue
        if not pois_written:
            for t in tests:
                w.add_node(osmium.osm.mutable.Node(
                    id=-1000 - t["id"], version=1, location=(t["poi"][1], t["poi"][0]),
                    tags={"name": f"TEST {t['id']}", "tourism": "attraction", "gsn:testpoint": "yes"}))
            # backup markers every ~60 m along each test road (in case the device hides the highlight line)
            for t in tests:
                cs = t.get("coords") or []
                for i, (la, lo) in enumerate(cs):
                    w.add_node(osmium.osm.mutable.Node(
                        id=-5000 - t["id"] * 100 - i, version=1, location=(lo, la),
                        tags={"name": f"T{t['id']}", "gsn:testmark": "yes"}))
            pois_written = True
        if o.is_way() and modify and o.id in by_way:
            t = by_way[o.id]
            tags = dict(o.tags)
            tags["name"] = LABEL[t["kind"]]
            if t["kind"] == "oneway":
                tags["oneway"] = "yes"
                tags.pop("oneway:bicycle", None)
            elif t["kind"] == "low_priority":
                tags["gsn:speed_max"] = "0"
                tags["gsn:class_max"] = "0"
            elif t["kind"] == "forbidden":
                tags["bicycle"] = "no"
            w.add_way(o.replace(tags=tags))
            # non-routable highlight copy, drawn as a thick line so it is easy to find on the Edge
            w.add_way(osmium.osm.mutable.Way(id=-2000 - t["id"], version=1, nodes=[n.ref for n in o.nodes],
                                             tags={"gsn:highlight": t["kind"], "name": LABEL[t["kind"]]}))
        elif o.is_way():
            w.add_way(o)
        elif o.is_relation():
            w.add_relation(o)
    w.close()


def draw_sheet(G: Graph, tests, center, path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, len(tests), figsize=(6 * len(tests), 7.5))
    axes = np.atleast_1d(axes)
    Mo = G.matrix()
    cidx = min(G.idx.values(), key=lambda i: hav(*center, *G.coord[G.rev[i]]))
    for ax, t in zip(axes, tests):
        for w in G.ways.values():
            ys, xs = zip(*w["coords"])
            ax.plot(xs, ys, color="#bbb", lw=0.6)
        ys, xs = zip(*G.ways[t["way"]]["coords"])
        col = {"oneway": "#d62", "low_priority": "#e9b000", "forbidden": "#c00"}[t["kind"]]
        ax.plot(xs, ys, color=col, lw=4, label=LABEL[t["kind"]])
        if t["kind"] == "oneway":
            ax.annotate("", xy=(xs[-1], ys[-1]), xytext=(xs[-2], ys[-2]),
                        arrowprops=dict(arrowstyle="-|>", color=col, lw=3))
        # expected route from the village centre with the restriction applied
        if t["kind"] == "oneway":
            Mt = G.matrix(oneway_override={t["way"]: 1})
            mid_node = min(G.ways[t["way"]]["nodes"],
                           key=lambda n: hav(*t["poi"], *G.coord[n]))
            tgt = G.idx[mid_node]
        else:
            Mt = G.matrix(removed={t["way"]})
            tgt = min(G.idx.values(), key=lambda i: hav(*t["poi"], *G.coord[G.rev[i]]))
        Dn, Pn = dijkstra(Mt, directed=True, indices=[cidx], return_predecessors=True)
        pts = G.path_coords(Pn[0], cidx, tgt)
        if pts:
            ys2, xs2 = zip(*pts)
            ax.plot(xs2, ys2, color="#1565c0", lw=2.5, label="expected route")
        Dn0, Pn0 = dijkstra(Mo, directed=True, indices=[cidx], return_predecessors=True)
        pts0 = G.path_coords(Pn0[0], cidx, tgt)
        if pts0:
            ys3, xs3 = zip(*pts0)
            ax.plot(xs3, ys3, color="#1565c0", lw=1.5, ls=":", label="route WITHOUT the rule (wrong)")
        allpts = (pts or []) + (pts0 or []) + [center, tuple(t["poi"])]
        la, lo = zip(*allpts)
        pad = 0.003
        ax.set_xlim(min(lo) - pad, max(lo) + pad)
        ax.set_ylim(min(la) - pad, max(la) + pad)
        ax.plot(center[1], center[0], "o", color="k", ms=8, label="Busa")
        ax.plot(t["poi"][1], t["poi"][0], "*", color="#7b1fa2", ms=18, label=f"TEST {t['id']}")
        ax.set_title(f"TEST {t['id']}: {t['explain']}")
        ax.set_aspect(1 / math.cos(math.radians(center[0])))
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.02), ncol=2, fontsize=9, frameon=False)
        ax.set_xticks([]); ax.set_yticks([])
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    G.matrix()


if __name__ == "__main__":
    main()
