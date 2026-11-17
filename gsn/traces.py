"""Parse cached OSM trackpoint pages, clean them, and keep only bicycle traces.

A trace is kept as a bike when, measured on the DEM:
  * on flats/descents it moves clearly faster than walking, and
  * it is not a car/motorbike (sustained high speeds).
Each kept trace is a list of cleaned segments (lat, lon, t arrays, 1 point per ~5-30 s).

Usage: python -m gsn.traces --area colli_euganei
"""
from __future__ import annotations

import argparse
import pickle
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml

from gsn.dem import Dem

NS = "{http://www.topografix.com/GPX/1/0}"
R = 6371000.0


@dataclass
class Trace:
    key: str
    user: str | None
    name: str | None
    segs: list = field(default_factory=list)       # list of np.ndarray (n, 3): lat, lon, t
    stats: dict = field(default_factory=dict)
    kind: str = "?"


def hav(lat1, lon1, lat2, lon2):
    p = np.pi / 180
    a = (np.sin((lat2 - lat1) * p / 2) ** 2
         + np.cos(lat1 * p) * np.cos(lat2 * p) * np.sin((lon2 - lon1) * p / 2) ** 2)
    return 2 * R * np.arcsin(np.sqrt(a))


def parse_pages(folder: Path) -> list[Trace]:
    traces: dict[str, Trace] = {}
    order = []
    anon = 0
    for page in sorted(folder.rglob("page_*.gpx")):
        root = ET.parse(page).getroot()
        for trk in root.findall(f"{NS}trk"):
            url = trk.findtext(f"{NS}url")
            name = trk.findtext(f"{NS}name")
            for seg in trk.findall(f"{NS}trkseg"):
                pts = []
                for p in seg.findall(f"{NS}trkpt"):
                    tt = p.findtext(f"{NS}time")
                    if tt is None:
                        continue
                    t = datetime.fromisoformat(tt.replace("Z", "+00:00")).timestamp()
                    pts.append((float(p.get("lat")), float(p.get("lon")), t))
                if len(pts) < 2:
                    continue
                if url:
                    key = url
                    user = re.search(r"/user/([^/]+)/", url)
                    user = user.group(1) if user else None
                else:
                    # 'trackable' traces: anonymous but ordered. Each segment = own key.
                    anon += 1
                    key, user = f"anon-{anon}", None
                if key not in traces:
                    traces[key] = Trace(key, user, name)
                    order.append(key)
                tr = traces[key]
                arr = np.array(pts)
                # a segment cut by a page boundary continues in the next page: stitch it
                if tr.segs and abs(arr[0, 2] - tr.segs[-1][-1, 2]) < 120:
                    tr.segs[-1] = np.vstack([tr.segs[-1], arr])
                else:
                    tr.segs.append(arr)
    return [traces[k] for k in order]


def clean_segment(a: np.ndarray, max_gap_s=300, max_jump_m=400, max_speed_kmh=90) -> list[np.ndarray]:
    a = a[np.argsort(a[:, 2], kind="stable")]
    keep = np.r_[True, np.diff(a[:, 2]) > 0]
    a = a[keep]
    if len(a) < 2:
        return []
    d = hav(a[:-1, 0], a[:-1, 1], a[1:, 0], a[1:, 1])
    dt = np.diff(a[:, 2])
    v = d / dt * 3.6
    # drop single-point spikes
    bad = np.zeros(len(a), bool)
    spike = (v[:-1] > max_speed_kmh) & (v[1:] > max_speed_kmh)
    bad[1:-1] = spike
    a = a[~bad]
    d = hav(a[:-1, 0], a[:-1, 1], a[1:, 0], a[1:, 1])
    dt = np.diff(a[:, 2])
    cut = np.where((dt > max_gap_s) | (d > max_jump_m) | (d / dt * 3.6 > max_speed_kmh))[0]
    parts = np.split(a, cut + 1)
    return [p for p in parts if len(p) >= 10]


def classify(tr: Trace, dem: Dem, cfg: dict) -> None:
    v_all, g_all, dts = [], [], []
    for s in tr.segs:
        d = hav(s[:-1, 0], s[:-1, 1], s[1:, 0], s[1:, 1])
        dt = np.diff(s[:, 2])
        z = dem.sample(s[:, 0], s[:, 1])
        cum = np.r_[0, np.cumsum(d)]
        # gradient over ~50 m around each step (on the DEM)
        mid = (cum[:-1] + cum[1:]) / 2
        lo = np.interp(mid - 25, cum, z)
        hi = np.interp(mid + 25, cum, z)
        span = np.minimum(mid + 25, cum[-1]) - np.maximum(mid - 25, 0)
        g = (hi - lo) / np.maximum(span, 1) * 100
        v_all.append(d / dt * 3.6)
        g_all.append(g)
        dts.append(dt)
    v, g, dt = np.concatenate(v_all), np.concatenate(g_all), np.concatenate(dts)
    moving = v > 1.5
    fd = moving & (g < 2) & np.isfinite(g)
    up = moving & (g > 4) & np.isfinite(g)
    st = {
        "points": int(sum(len(s) for s in tr.segs)),
        "km": float(sum(hav(s[:-1, 0], s[:-1, 1], s[1:, 0], s[1:, 1]).sum() for s in tr.segs) / 1000),
        "median_dt": float(np.median(dt)),
        "flatdown_kmh": float(np.median(v[fd])) if fd.sum() > 20 else float("nan"),
        "up_kmh": float(np.median(v[up])) if up.sum() > 20 else float("nan"),
        "p90_kmh": float(np.percentile(v[moving], 90)) if moving.sum() > 20 else float("nan"),
    }
    tr.stats = st
    c = cfg
    if st["km"] < c["min_km"] or st["median_dt"] > c["max_median_dt_s"]:
        tr.kind = "unusable"
    elif not np.isfinite(st["flatdown_kmh"]):
        tr.kind = "unusable"
    elif st["p90_kmh"] > c["car_p90_kmh"] or st["flatdown_kmh"] > c["car_flatdown_kmh"]:
        tr.kind = "motor"
    elif st["flatdown_kmh"] < c["hike_flatdown_kmh"]:
        tr.kind = "hike"
    elif st["flatdown_kmh"] >= c["bike_flatdown_kmh"]:
        tr.kind = "bike"
    else:
        tr.kind = "ambiguous"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", required=True)
    ap.add_argument("--config", type=Path, default=Path("config.yaml"))
    a = ap.parse_args()
    cfg = yaml.safe_load(a.config.read_text())
    bbox = cfg["areas"][a.area]["bbox"]
    tcfg = cfg["traces"]
    dem = Dem(Path("cache/dem") / cfg["sources"]["dem"], bbox)

    traces = parse_pages(Path("cache/traces") / a.area)
    raw_pts = sum(len(s) for t in traces for s in t.segs)
    for t in traces:
        t.segs = [p for s in t.segs for p in clean_segment(s)]
    traces = [t for t in traces if t.segs]
    for t in traces:
        classify(t, dem, tcfg)

    kinds = Counter(t.kind for t in traces)
    km = Counter()
    for t in traces:
        km[t.kind] += t.stats["km"]
    users = len({t.user for t in traces if t.kind == "bike" and t.user})
    print(f"timestamped points: {raw_pts:,}   traces after cleaning: {len(traces)}")
    for k in ("bike", "hike", "motor", "ambiguous", "unusable"):
        print(f"  {k:10s} {kinds[k]:5d} traces  {km[k]:8.0f} km")
    print(f"  bike traces from {users} identifiable users (+ anonymous 'trackable' ones)")
    bikes = [t for t in traces if t.kind == "bike"]
    if bikes:
        fd = np.array([t.stats["flatdown_kmh"] for t in bikes])
        up = np.array([t.stats["up_kmh"] for t in bikes if np.isfinite(t.stats["up_kmh"])])
        print(f"  bike flat/down median speed: {np.median(fd):.1f} km/h, uphill median: {np.median(up):.1f} km/h")
    out = Path("cache/traces_proc")
    out.mkdir(parents=True, exist_ok=True)
    with open(out / f"{a.area}.pkl", "wb") as f:
        pickle.dump([{"key": t.key, "user": t.user, "name": t.name, "segs": t.segs,
                      "stats": t.stats, "kind": t.kind} for t in traces], f)


if __name__ == "__main__":
    main()
