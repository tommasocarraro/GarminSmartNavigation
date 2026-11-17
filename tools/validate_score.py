"""Check the scoring against the rider's own FIT rides: what they actually rode must stay usable.

For every segment the ride traverses (in the ridden direction) it reports km by outcome:
blocked (no bikes / against a oneway we added) and road_speed. Climbs (>= 6 % avg in the
ridden direction) are reported separately, with the list of blocked/penalised climbs.

Usage: python tools/validate_score.py --area colli_euganei my_rides/*.zip
"""
from __future__ import annotations

import argparse
import pickle
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from calibrate import read_fit  # noqa: E402
from gsn.match import Matcher  # noqa: E402
from gsn.segments import TRAIL  # noqa: E402


def outcome(s, f, d):
    if f["bike"] == "no":
        return "NO BIKES"
    if s["highway"] in ("footway", "pedestrian", "steps") and "bicycle" not in s["tags"] and f["bike"] != "yes":
        return "NO BIKES"     # mkgmap default access: pedestrian only
    if (f["oneway"] == "yes" and d < 0) or (f["oneway"] == "-1" and d > 0):
        return "BLOCKED dir"
    return f"speed {f['speed']}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", required=True)
    ap.add_argument("files", nargs="+", type=Path)
    a = ap.parse_args()
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    l, b, r, t = cfg["areas"][a.area]["bbox"]
    d = pickle.load(open(f"cache/scored/{a.area}.pkl", "rb"))
    segs, final = d["segs"], d["final"]
    m = Matcher(segs)
    allk, climbk, trailk = Counter(), Counter(), Counter()
    bad = defaultdict(float)
    for f in sorted(a.files):
        fit = read_fit(f)
        inside = (fit["lat"] > b) & (fit["lat"] < t) & (fit["lon"] > l) & (fit["lon"] < r)
        if inside.mean() < 0.9:
            continue
        trav, frac = m.traversals(np.c_[fit["lat"], fit["lon"], fit["t"]])
        seen = set()
        for g, dd, v in trav:
            if (g, dd) in seen:
                continue
            seen.add((g, dd))
            s, fn = segs[g], final[g]
            o = outcome(s, fn, dd)
            L = s["len_m"] / 1000
            allk[o] += L
            if s["highway"] in TRAIL:
                trailk[o] += L
                if s["grad_avg"] * dd >= 6:
                    climbk[o] += L
                    if o.startswith(("NO", "BLOCKED")) or fn["speed"] <= 1:
                        bad[(s["way"], s["tags"].get("name", ""), s["highway"], o, fn["why"])] += L
        print(f"{f.stem}: matched {frac:.0%}")

    def show(title, c):
        tot = sum(c.values())
        print(f"\n{title}: {tot:.1f} km")
        for k in sorted(c):
            print(f"  {k:12s} {c[k]:6.2f} km  {c[k] / tot:5.1%}")
    show("All ridden segments", allk)
    show("Ridden trails", trailk)
    show("Ridden trail CLIMBS (>= 6 %)", climbk)
    print("\nClimbs you rode that are now blocked or speed <= 1:")
    for (w, name, hw, o, why), L in sorted(bad.items(), key=lambda x: -x[1]):
        print(f"  way {w:>11} {hw:8s} {name[:24]:24s} {L * 1000:5.0f} m  {o:11s} {why}")


if __name__ == "__main__":
    main()
