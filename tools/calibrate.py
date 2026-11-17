"""Derive climbing limits from the rider's own FIT files.

For every ride: resample the track on a fixed distance grid, compute the
gradient over sliding windows (50 m ... 1 km) while actually riding, and report
how steep the rider climbs. Lift/shuttle sections (vertical speed impossible
on a bike) and stops are excluded; probable push sections are flagged.

Usage:
    python tools/calibrate.py my_rides/*.zip --exclude 24930631650 --out out/calibration
"""
from __future__ import annotations

import argparse
import io
import json
import zipfile
from pathlib import Path

import fitdecode
import numpy as np

SEMI = 180.0 / 2**31
STEP = 5.0                      # m, resampling grid
WINDOWS = [50, 100, 200, 500, 1000]
MAX_VERT_RATE = 1800.0          # m/h; above this when climbing = lift/shuttle
MIN_SPEED_KMH = 2.0             # window slower than this = stopped
PUSH_SPEED_KMH = 4.5            # steep + slower than this (and no cadence) = probably pushing


def read_fit(path: Path) -> dict:
    data = path.read_bytes()
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".fit"))
            data = z.read(name)
    rows, sport = [], {}
    with fitdecode.FitReader(io.BytesIO(data)) as r:
        for fr in r:
            if not isinstance(fr, fitdecode.FitDataMessage):
                continue
            if fr.name == "sport":
                sport = {f.name: f.value for f in fr.fields}
            elif fr.name == "record":
                d = {f.name: f.value for f in fr.fields}
                alt = d.get("enhanced_altitude", d.get("altitude"))
                if d.get("position_lat") is None or d.get("distance") is None or alt is None:
                    continue
                rows.append((d["timestamp"].timestamp(), d["position_lat"] * SEMI,
                             d["position_long"] * SEMI, float(d["distance"]), float(alt),
                             d.get("cadence")))
    a = np.array([r[:5] for r in rows], dtype=float)
    cad = np.array([np.nan if r[5] is None else r[5] for r in rows], dtype=float)
    return {"t": a[:, 0], "lat": a[:, 1], "lon": a[:, 2], "dist": a[:, 3], "alt": a[:, 4],
            "cad": cad, "sport": sport}


def median_filter(x: np.ndarray, k: int = 5) -> np.ndarray:
    pad = k // 2
    xp = np.pad(x, pad, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(xp, k), axis=1)


def resample(r: dict) -> dict:
    d = np.maximum.accumulate(r["dist"])
    keep = np.r_[True, np.diff(d) > 0.05]
    d = d[keep]
    alt = median_filter(r["alt"][keep])
    grid = np.arange(d[0], d[-1], STEP)
    out = {"d": grid}
    for k in ("t", "lat", "lon", "cad"):
        out[k] = np.interp(grid, d, r[k][keep])
    out["alt"] = np.interp(grid, d, alt)
    return out


def windows(g: dict, L: int) -> dict:
    n = int(L / STEP)
    if len(g["d"]) <= n:
        return {}
    i0, i1 = np.arange(len(g["d"]) - n), np.arange(n, len(g["d"]))
    dz = g["alt"][i1] - g["alt"][i0]
    dt = g["t"][i1] - g["t"][i0]
    grad = dz / L * 100.0
    speed = L / np.maximum(dt, 1e-3) * 3.6
    vrate = dz / np.maximum(dt, 1e-3) * 3600.0
    cs = np.r_[0, np.cumsum(np.nan_to_num(g["cad"]) > 0)]
    has_cad = ~np.all(np.isnan(g["cad"]))
    pedal_frac = (cs[i1] - cs[i0]) / n if has_cad else np.full(len(i0), np.nan)
    riding = (speed >= MIN_SPEED_KMH) & (vrate <= MAX_VERT_RATE)
    return {"i0": i0, "grad": grad, "speed": speed, "riding": riding, "pedal": pedal_frac}


def pct(x, q):
    return float(np.percentile(x, q)) if len(x) else float("nan")


def top_walls(g, w, L, k=5):
    """Steepest non-overlapping ridden windows."""
    order = np.argsort(-np.where(w["riding"], w["grad"], -999))
    taken, res = np.zeros(len(g["d"]), bool), []
    n = int(L / STEP)
    for j in order:
        if not w["riding"][j] or w["grad"][j] <= 0 or len(res) >= k:
            break
        a = w["i0"][j]
        if taken[a:a + n + 1].any():
            continue
        taken[a:a + n + 1] = True
        res.append({"grad": round(float(w["grad"][j]), 1), "speed_kmh": round(float(w["speed"][j]), 1),
                    "pedal_frac": None if np.isnan(w["pedal"][j]) else round(float(w["pedal"][j]), 2),
                    "lat": round(float(g["lat"][a]), 5), "lon": round(float(g["lon"][a]), 5),
                    "km": round(float(g["d"][a]) / 1000, 2)})
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--exclude", nargs="*", default=[], help="ride ids to skip (e.g. lift-assisted DH)")
    ap.add_argument("--out", type=Path, default=Path("out/calibration"))
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    pooled = {L: {"grad": [], "speed": []} for L in WINDOWS}
    report = {"rides": {}, "pooled": {}}
    for f in sorted(a.files):
        rid = f.stem.split("_")[0]
        if rid in a.exclude:
            continue
        g = resample(read_fit(f))
        ride = {"km": round(float(g["d"][-1] - g["d"][0]) / 1000, 1)}
        for L in WINDOWS:
            w = windows(g, L)
            if not w:
                continue
            up = w["riding"] & (w["grad"] > 1.0)
            if L == 50:
                ride["excluded_climb_km"] = round(float(np.sum(w["grad"] > 1) - np.sum(up & (w["speed"] >= MIN_SPEED_KMH))) * STEP / 1000, 2)
            ride[f"{L}m"] = {"p50": round(pct(w["grad"][up], 50), 1), "p90": round(pct(w["grad"][up], 90), 1),
                             "p99": round(pct(w["grad"][up], 99), 1), "max": round(float(w["grad"][up].max()), 1) if up.any() else None,
                             "top": top_walls(g, w, L)}
            pooled[L]["grad"].append(w["grad"][up])
            pooled[L]["speed"].append(w["speed"][up])
        report["rides"][rid] = ride

    for L in WINDOWS:
        gr, sp = np.concatenate(pooled[L]["grad"]), np.concatenate(pooled[L]["speed"])
        bins = [(0, 5), (5, 10), (10, 15), (15, 20), (20, 25), (25, 30), (30, 35), (35, 100)]
        report["pooled"][f"{L}m"] = {
            "km_climbing": round(len(gr) * STEP / 1000, 1),
            "p50": round(pct(gr, 50), 1), "p90": round(pct(gr, 90), 1), "p95": round(pct(gr, 95), 1),
            "p99": round(pct(gr, 99), 1), "p99.9": round(pct(gr, 99.9), 1), "max": round(float(gr.max()), 1),
            "by_grad": {f"{lo}-{hi}%": {"km": round(float(np.sum((gr >= lo) & (gr < hi))) * STEP / 1000, 2),
                                        "median_kmh": round(float(np.median(sp[(gr >= lo) & (gr < hi)])), 1)
                                        if np.any((gr >= lo) & (gr < hi)) else None}
                        for lo, hi in bins},
        }
    (a.out / "calibration.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report["pooled"], indent=1))
    for rid, r in report["rides"].items():
        print(rid, r["km"], "km | excluded (stops/lift)", r.get("excluded_climb_km"), "km |",
              " ".join(f"{L}m max {r[f'{L}m']['max']} p90 {r[f'{L}m']['p90']}" for L in WINDOWS if f"{L}m" in r))
        print("   top 50m:", [(t["grad"], t["speed_kmh"], t["pedal_frac"], t["lat"], t["lon"]) for t in r["50m"]["top"]])


if __name__ == "__main__":
    main()
