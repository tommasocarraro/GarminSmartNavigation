"""Check routes exported from BaseCamp against our scores and the rider's FIT rides.

For each route: the segments it uses (snapped like a GPS trace), in which direction,
their score/why, flags (no bikes, against our oneway, speed <= 1, steep climbs),
and how much of it the rider has ridden in the same direction.

Usage: python tools/check_routes.py --area colli_euganei out/colli_euganei/basecamp_routes.gpx my_rides/*.zip
"""
from __future__ import annotations

import argparse
import pickle
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from calibrate import read_fit  # noqa: E402
from gsn.match import Matcher  # noqa: E402


def read_routes(gpx: Path):
    s = gpx.read_text()
    out = []
    for rte in re.findall(r"<rte>(.*?)</rte>", s, re.S):
        name = re.search(r"<name>(.*?)</name>", rte).group(1)
        pts = [(float(a), float(b)) for a, b in
               re.findall(r'<(?:rtept|gpxx:rpt) lat="([-\d.]+)" lon="([-\d.]+)"', rte)]
        out.append((name, np.array(pts)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", required=True)
    ap.add_argument("gpx", type=Path)
    ap.add_argument("rides", nargs="*", type=Path)
    a = ap.parse_args()
    d = pickle.load(open(f"cache/scored/{a.area}.pkl", "rb"))
    segs, final = d["segs"], d["final"]
    m = Matcher(segs, max_dist=12, min_cover=0.5)
    ridden = set()
    for f in a.rides:
        fit = read_fit(f)
        for g, dd, _ in m.traversals(np.c_[fit["lat"], fit["lon"], fit["t"]])[0]:
            ridden.add((g, dd))
    for name, pts in read_routes(a.gpx):
        arr = np.c_[pts, np.arange(len(pts), dtype=float)]
        trav, frac = m.traversals(arr)
        seen, rows = set(), []
        for g, dd, _ in trav:
            if (g, dd) not in seen:
                seen.add((g, dd))
                rows.append((g, dd))
        tot = sum(segs[g]["len_m"] for g, _ in rows)
        rid = sum(segs[g]["len_m"] for g, dd in rows if (g, dd) in ridden)
        print(f"\n=== {name}: {len(pts)} pts, matched {frac:.0%}, {tot / 1000:.2f} km, "
              f"ridden by you same direction {rid / max(tot, 1):.0%}")
        print(f"{'way':>11} {'hw':9} {'len':>5} {'grade':>6} {'w100':>5} spd cls flags  why")
        for g, dd in rows:
            s, f = segs[g], final[g]
            grade = s["grad_avg"] * dd
            w100 = s["worst100_fwd" if dd > 0 else "worst100_bwd"]
            flags = ""
            if f["bike"] == "no":
                flags += "NOBIKE "
            if (f["oneway"] == "yes" and dd < 0) or (f["oneway"] == "-1" and dd > 0):
                flags += "AGAINST "
            if f["speed"] <= 1:
                flags += "SLOW "
            if (g, dd) in ridden:
                flags += "ridden "
            nm = s["tags"].get("name", "")[:18]
            print(f"{s['way']:>11} {s['highway'][:9]:9} {s['len_m']:5.0f} {grade:+6.1f} {w100:5.1f} "
                  f"{f['speed']:3d} {f['cls']:3d} {flags:14s} {nm} {f['why']}")


if __name__ == "__main__":
    main()
