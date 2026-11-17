"""Split routable ways into short segments and compute terrain metrics per direction.

Ways are cut at every junction, then into equal pieces of at most `max_len` m.
For each segment (direction = OSM node order, 'fwd'; reverse = 'bwd'):
  len_m, climb_fwd/bwd (m), grad_avg (signed, fwd), worst50_fwd/bwd, worst100_fwd/bwd,
  rough (m, std of DEM profile residual after 50 m smoothing)
Worst-window gradients are measured over the segment plus 50 m context on each side
along the same way, so a short wall that straddles a cut is not missed.

Usage: python -m gsn.segments --area colli_euganei
"""
from __future__ import annotations

import argparse
import math
import pickle
from pathlib import Path

import numpy as np
import osmium
import yaml

from gsn.dem import Dem

ROUTABLE = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
            "living_street", "service", "track", "path", "footway", "bridleway", "cycleway", "steps",
            "pedestrian", "road", "primary_link", "secondary_link", "tertiary_link", "trunk_link",
            "motorway_link"}
TRAIL = {"path", "track", "footway", "bridleway", "cycleway", "steps"}
STEP = 5.0
R = 6371000.0


def hav(lat1, lon1, lat2, lon2):
    p = np.pi / 180
    a = (np.sin((lat2 - lat1) * p / 2) ** 2
         + np.cos(lat1 * p) * np.cos(lat2 * p) * np.sin((lon2 - lon1) * p / 2) ** 2)
    return 2 * R * np.arcsin(np.sqrt(a))


def load_ways(pbf: Path):
    ways, count = {}, {}
    for o in osmium.FileProcessor(str(pbf)).with_locations():
        if not o.is_way():
            continue
        t = dict(o.tags)
        if t.get("highway") not in ROUTABLE or t.get("area") == "yes":
            continue
        try:
            coords = np.array([(n.lat, n.lon) for n in o.nodes])
        except osmium.InvalidLocationError:
            continue
        nodes = [n.ref for n in o.nodes]
        if len(nodes) < 2:
            continue
        ways[o.id] = (nodes, coords, t)
        for i, n in enumerate(nodes):
            count[n] = count.get(n, 0) + (2 if 0 < i < len(nodes) - 1 else 1)
    return ways, count


def resample_line(coords: np.ndarray, step: float):
    d = hav(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])
    cum = np.r_[0, np.cumsum(d)]
    L = cum[-1]
    n = max(2, int(math.ceil(L / step)) + 1)
    s = np.linspace(0, L, n)
    return s, np.interp(s, cum, coords[:, 0]), np.interp(s, cum, coords[:, 1]), cum


def worst(s, z, a, b, win):
    """Steepest uphill over `win` m (both directions) for windows overlapping [a, b]."""
    if s[-1] < win:
        win = max(s[-1], STEP)
    zi = np.interp(np.arange(0, s[-1] - win + 1e-6, STEP) + win, s, z)
    z0 = np.interp(np.arange(0, s[-1] - win + 1e-6, STEP), s, z)
    st = np.arange(0, s[-1] - win + 1e-6, STEP)
    m = (st + win > a) & (st < b)
    if not m.any():
        return 0.0, 0.0
    g = (zi - z0)[m] / win * 100
    return float(max(g.max(), 0)), float(max((-g).max(), 0))


def build(pbf: Path, dem: Dem, max_len: float):
    ways, count = load_ways(pbf)
    segs = []
    for wid, (nodes, coords, tags) in ways.items():
        cut = [0] + [i for i in range(1, len(nodes) - 1) if count.get(nodes[i], 0) > 2] + [len(nodes) - 1]
        s_all, la, lo, cum = resample_line(coords, STEP)
        z = dem.sample(la, lo)
        if np.isnan(z).any():
            if np.isnan(z).all():
                z = np.zeros_like(z)
            else:
                ok = ~np.isnan(z)
                z = np.interp(s_all, s_all[ok], z[ok])
        zs = np.convolve(np.pad(z, 5, mode="edge"), np.ones(11) / 11, mode="valid")  # ~50 m smoothing
        for a_i, b_i in zip(cut[:-1], cut[1:]):
            a, b = cum[a_i], cum[b_i]
            L = b - a
            n = max(1, int(math.ceil(L / max_len)))
            for k in range(n):
                sa, sb = a + L * k / n, a + L * (k + 1) / n
                m = (s_all >= sa - 1e-6) & (s_all <= sb + 1e-6)
                idx = np.where(m)[0]
                if len(idx) < 2:
                    idx = np.array([max(0, np.searchsorted(s_all, sa) - 1), min(len(s_all) - 1, np.searchsorted(s_all, sb))])
                za, zb = np.interp(sa, s_all, z), np.interp(sb, s_all, z)
                prof = z[idx]
                dz = np.diff(prof)
                ctx = (s_all >= sa - 50) & (s_all <= sb + 50)
                sc, zc = s_all[ctx] - s_all[ctx][0], z[ctx]
                off = sa - s_all[ctx][0]
                w50 = worst(sc, zc, off, off + (sb - sa), 50)
                w100 = worst(sc, zc, off, off + (sb - sa), 100)
                segs.append({
                    "id": len(segs), "way": wid, "k": k, "highway": tags.get("highway"), "tags": tags,
                    "lat": np.interp(np.r_[s_all[idx]], s_all, la), "lon": np.interp(np.r_[s_all[idx]], s_all, lo),
                    "len_m": float(sb - sa),
                    "grad_avg": float((zb - za) / max(sb - sa, 1) * 100),
                    "climb_fwd": float(dz[dz > 0].sum()), "climb_bwd": float(-dz[dz < 0].sum()),
                    "worst50_fwd": w50[0], "worst50_bwd": w50[1],
                    "worst100_fwd": w100[0], "worst100_bwd": w100[1],
                    "rough": float(np.std(z[idx] - zs[idx])),
                })
    return segs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", required=True)
    ap.add_argument("--config", type=Path, default=Path("config.yaml"))
    ap.add_argument("--max-len", type=float, default=100.0)
    a = ap.parse_args()
    cfg = yaml.safe_load(a.config.read_text())
    bbox = cfg["areas"][a.area]["bbox"]
    dem = Dem(Path("cache/dem") / cfg["sources"]["dem"], bbox)
    segs = build(Path("cache/areas") / f"{a.area}.osm.pbf", dem, a.max_len)
    out = Path("cache/segments")
    out.mkdir(parents=True, exist_ok=True)
    with open(out / f"{a.area}.pkl", "wb") as f:
        pickle.dump(segs, f)
    trail = [s for s in segs if s["highway"] in TRAIL]
    print(f"{len(segs)} segments ({sum(s['len_m'] for s in segs)/1000:.0f} km), "
          f"trails: {len(trail)} ({sum(s['len_m'] for s in trail)/1000:.0f} km)")
    w = np.array([max(s["worst100_fwd"], s["worst100_bwd"]) for s in trail])
    print("trail worst-100m gradient percentiles 50/90/99:", np.percentile(w, [50, 90, 99]).round(1))


if __name__ == "__main__":
    main()
