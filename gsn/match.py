"""Match bike traces to segments and count independent traversals per direction.

Matching: each trace is resampled every 5 m; every trace point is snapped to the nearest
segment sample point (within `max_dist` m). Consecutive points on the same segment form a
run; a run that covers >= `min_cover` of the segment length is a traversal, with direction
given by how the position along the segment changes. Each trace counts once per
segment per direction (independent traces).

Usage: python -m gsn.match --area colli_euganei
"""
from __future__ import annotations

import argparse
import pickle
from collections import defaultdict
from pathlib import Path

import numpy as np
import yaml
from scipy.spatial import cKDTree

from gsn.segments import TRAIL, hav

LAT0 = 45.5


def xy(lat, lon):
    k = np.cos(np.radians(LAT0)) * 111320.0
    return np.c_[np.asarray(lon) * k, np.asarray(lat) * 110540.0]


def resample_trace(seg: np.ndarray, step=5.0):
    d = hav(seg[:-1, 0], seg[:-1, 1], seg[1:, 0], seg[1:, 1])
    cum = np.r_[0, np.cumsum(d)]
    if cum[-1] < step * 2:
        return None
    s = np.arange(0, cum[-1], step)
    return (np.interp(s, cum, seg[:, 0]), np.interp(s, cum, seg[:, 1]), np.interp(s, cum, seg[:, 2]))


class Matcher:
    """Snap 5 m-resampled traces to the nearest segment; yield (segment, direction, speed) traversals."""

    def __init__(self, segs, max_dist=15.0, min_cover=0.6):
        P, sid, pos = [], [], []
        for s in segs:
            p = xy(s["lat"], s["lon"])
            dd = np.r_[0, np.cumsum(np.hypot(*np.diff(p, axis=0).T))]
            P.append(p); sid.append(np.full(len(p), s["id"])); pos.append(dd)
        self.sid, self.pos = np.concatenate(sid), np.concatenate(pos)
        self.tree = cKDTree(np.vstack(P))
        self.seglen = np.array([s["len_m"] for s in segs])
        self.max_dist, self.min_cover = max_dist, min_cover

    def traversals(self, seg_arr):
        """seg_arr: (n, 3) lat, lon, t. Returns list of (segment id, +1/-1, speed km/h), and matched fraction."""
        r = resample_trace(seg_arr)
        if r is None:
            return [], 0.0
        la, lo, tt = r
        dist, j = self.tree.query(xy(la, lo), distance_upper_bound=self.max_dist)
        ok = np.isfinite(dist)
        jj = np.minimum(j, len(self.sid) - 1)
        ids = np.where(ok, self.sid[jj], -1)
        ps = np.where(ok, self.pos[jj], np.nan)
        out, used = [], 0
        i, n = 0, len(ids)
        while i < n:
            if ids[i] < 0:
                i += 1
                continue
            k = i
            while k + 1 < n and ids[k + 1] == ids[i]:
                k += 1
            g = ids[i]
            p0, p1 = ps[i], ps[k]
            covered = np.nanmax(ps[i:k + 1]) - np.nanmin(ps[i:k + 1])
            L = self.seglen[g]
            if covered >= self.min_cover * L or (L < 15 and k > i):
                dt = tt[k] - tt[i]
                v = abs(p1 - p0) / dt * 3.6 if dt > 0 else np.nan
                if p1 != p0:
                    out.append((int(g), 1 if p1 > p0 else -1, v))
                    used += k - i + 1
            i = k + 1
        return out, used / n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", required=True)
    ap.add_argument("--config", type=Path, default=Path("config.yaml"))
    ap.add_argument("--max-dist", type=float, default=15.0)
    ap.add_argument("--min-cover", type=float, default=0.6)
    a = ap.parse_args()
    cfg = yaml.safe_load(a.config.read_text())
    segs = pickle.load(open(f"cache/segments/{a.area}.pkl", "rb"))
    from types import SimpleNamespace
    traces = [SimpleNamespace(**t) for t in pickle.load(open(f"cache/traces_proc/{a.area}.pkl", "rb"))
              if t["kind"] == "bike"]

    m = Matcher(segs, a.max_dist, a.min_cover)
    fwd, bwd = defaultdict(set), defaultdict(set)
    users_f, users_b = defaultdict(set), defaultdict(set)
    speed_f, speed_b = defaultdict(list), defaultdict(list)
    for t in traces:
        who = t.user or t.key
        for seg in t.segs:
            for g, d, v in m.traversals(seg)[0]:
                if d > 0:
                    fwd[g].add(t.key); users_f[g].add(who); speed_f[g].append(v)
                else:
                    bwd[g].add(t.key); users_b[g].add(who); speed_b[g].append(v)

    for s in segs:
        g = s["id"]
        s["n_fwd"], s["n_bwd"] = len(fwd[g]), len(bwd[g])
        s["u_fwd"], s["u_bwd"] = len(users_f[g]), len(users_b[g])
        s["v_fwd"] = float(np.nanmedian(speed_f[g])) if speed_f[g] else None
        s["v_bwd"] = float(np.nanmedian(speed_b[g])) if speed_b[g] else None
    with open(f"cache/segments/{a.area}.pkl", "wb") as f:
        pickle.dump(segs, f)
    report(segs)


def report(segs):
    def km(sel):
        return sum(s["len_m"] for s in sel) / 1000
    trail = [s for s in segs if s["highway"] in TRAIL]
    tot = km(trail)
    print(f"Trails: {tot:.0f} km")
    for th in (1, 3, 10):
        sel = [s for s in trail if s["n_fwd"] + s["n_bwd"] >= th]
        print(f"  ridden by >= {th:2d} bike traces (any direction): {km(sel):6.0f} km  ({km(sel)/tot:.0%})")
    # climbs: segment direction that goes up >= 6 % on average
    up = []
    for s in trail:
        if s["grad_avg"] >= 6:
            up.append((s, s["n_fwd"], s["n_bwd"]))
        elif s["grad_avg"] <= -6:
            up.append((s, s["n_bwd"], s["n_fwd"]))
    tot_up = sum(s["len_m"] for s, _, _ in up) / 1000
    print(f"Trail climbs (>= 6 %): {tot_up:.0f} km")
    for th in (1, 3):
        sel = [s for s, u, d in up if u >= th]
        print(f"  ridden UPHILL by >= {th} traces: {km(sel):5.0f} km ({km(sel)/tot_up:.0%})")
    sel = [s for s, u, d in up if u == 0 and d >= 3]
    print(f"  only ridden DOWNHILL (>= 3 traces, none up): {km(sel):5.0f} km ({km(sel)/tot_up:.0%})")
    sel = [s for s, u, d in up if u + d == 0]
    print(f"  no bike traffic at all: {km(sel):5.0f} km ({km(sel)/tot_up:.0%})")
    roads = [s for s in segs if s["highway"] not in TRAIL]
    r1 = [s for s in roads if s["n_fwd"] + s["n_bwd"] >= 1]
    print(f"Roads: {km(roads):.0f} km, ridden by >= 1 trace: {km(r1)/km(roads):.0%}")


if __name__ == "__main__":
    main()
