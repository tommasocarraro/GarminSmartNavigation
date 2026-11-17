"""Download OSM public GPS trackpoints for a bbox, page by page, with on-disk cache.

API: /api/0.6/trackpoints?bbox=l,b,r,t&page=N  (5000 points/page, bbox <= 0.25 deg²).
Large bboxes are tiled. Every page is cached, so the run can be interrupted and resumed.

Usage: python -m gsn.trace_download --bbox 11.60 45.23 11.80 45.37 --name colli_euganei
"""
from __future__ import annotations

import argparse
import math
import time
from pathlib import Path

import requests

API = "https://api.openstreetmap.org/api/0.6/trackpoints"
UA = "GarminSmartNavigation/0.1 (personal e-MTB routing research)"
PAGE_POINTS = 5000


def tiles(l, b, r, t, size=0.45):
    """Split bbox into tiles of size x size deg (0.45² ≈ 0.2 deg² < 0.25 limit)."""
    nx, ny = math.ceil((r - l) / size), math.ceil((t - b) / size)
    for i in range(nx):
        for j in range(ny):
            yield (round(l + i * size, 5), round(b + j * size, 5),
                   round(min(r, l + (i + 1) * size), 5), round(min(t, b + (j + 1) * size), 5))


def fetch_tile(bbox, outdir: Path, delay: float, session: requests.Session, max_pages: int):
    tdir = outdir / "_".join(f"{v:.5f}" for v in bbox)
    tdir.mkdir(parents=True, exist_ok=True)
    if (tdir / "COMPLETE").exists():
        return
    page = 0
    while page < max_pages:
        f = tdir / f"page_{page:05d}.gpx"
        if not f.exists():
            for attempt in range(6):
                try:
                    resp = session.get(API, params={"bbox": ",".join(map(str, bbox)), "page": page}, timeout=120)
                except requests.RequestException:
                    resp = None
                if resp is not None and resp.status_code == 200:
                    break
                wait = 30 * (attempt + 1)
                print(f"  page {page}: {getattr(resp, 'status_code', 'net error')}, retry in {wait}s", flush=True)
                time.sleep(wait)
            else:
                raise RuntimeError(f"giving up on {bbox} page {page}")
            tmp = f.with_suffix(".part")
            tmp.write_bytes(resp.content)
            tmp.rename(f)
            time.sleep(delay)
        npts = f.read_bytes().count(b"<trkpt")
        print(f"  {bbox} page {page}: {npts} pts", flush=True)
        if npts < PAGE_POINTS:
            (tdir / "COMPLETE").touch()
            return
        page += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("L", "B", "R", "T"))
    ap.add_argument("--name", required=True)
    ap.add_argument("--cache", type=Path, default=Path("cache/traces"))
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--max-pages", type=int, default=100000)
    a = ap.parse_args()
    s = requests.Session()
    s.headers["User-Agent"] = UA
    for bb in tiles(*a.bbox):
        fetch_tile(bb, a.cache / a.name, a.delay, s, a.max_pages)
    print("done")


if __name__ == "__main__":
    main()
