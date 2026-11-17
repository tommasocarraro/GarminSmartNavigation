"""Validate the trace matcher with the rider's own FIT rides (known bike, known climbs).

Reports per ride: share of the ride matched to segments, and for steep segments whether
the matched direction agrees with the barometric climb/descent.

Usage: python tools/validate_match.py --area colli_euganei my_rides/*.zip
"""
from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from calibrate import read_fit  # noqa: E402
from gsn.match import Matcher  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", required=True)
    ap.add_argument("files", nargs="+", type=Path)
    a = ap.parse_args()
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    l, b, r, t = cfg["areas"][a.area]["bbox"]
    segs = pickle.load(open(f"cache/segments/{a.area}.pkl", "rb"))
    m = Matcher(segs)
    tot_agree = tot_steep = 0
    for f in sorted(a.files):
        fit = read_fit(f)
        inside = (fit["lat"] > b) & (fit["lat"] < t) & (fit["lon"] > l) & (fit["lon"] < r)
        if inside.mean() < 0.9:
            continue
        arr = np.c_[fit["lat"], fit["lon"], fit["t"]]
        trav, frac = m.traversals(arr)
        # direction check: DEM gradient of the segment in the matched direction vs barometer
        agree = steep = 0
        for g, d, v in trav:
            s = segs[g]
            gr = s["grad_avg"] * d
            if abs(gr) < 6 or s["len_m"] < 40:
                continue
            steep += 1
            # nearest FIT points to segment start/end in matched order
            la0, lo0 = (s["lat"][0], s["lon"][0]) if d > 0 else (s["lat"][-1], s["lon"][-1])
            la1, lo1 = (s["lat"][-1], s["lon"][-1]) if d > 0 else (s["lat"][0], s["lon"][0])
            i0 = np.argmin((fit["lat"] - la0) ** 2 + (fit["lon"] - lo0) ** 2)
            i1 = np.argmin((fit["lat"] - la1) ** 2 + (fit["lon"] - lo1) ** 2)
            baro = fit["alt"][i1] - fit["alt"][i0]
            agree += np.sign(baro) == np.sign(gr)
        tot_agree += agree
        tot_steep += steep
        print(f"{f.stem.split('_')[0]}: matched {frac:.0%} of ride, {len(trav)} segment traversals, "
              f"direction agrees with barometer on {agree}/{steep} steep segments")
    if tot_steep:
        print(f"TOTAL direction agreement: {tot_agree}/{tot_steep} = {tot_agree / tot_steep:.0%}")


if __name__ == "__main__":
    main()
