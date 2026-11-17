"""Compare DEM-derived gradients with the barometric gradients of the FIT rides.

The map scores segments from a DEM, but the rider's limits were measured with a
barometer. DEMs smooth short walls (and GLO-30 includes tree canopy), so the
same physical climb reads differently. This script:
  * reports elevation error (after removing each ride's baro offset) per DEM
  * quantile-maps barometric gradient thresholds to DEM-equivalent thresholds

Usage:
    python tools/dem_vs_baro.py my_rides/*.zip --exclude 24930631650 \
        --dem tinitaly=cache/dem/tinitaly --dem glo30=cache/dem/glo30
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.merge import merge

sys.path.insert(0, str(Path(__file__).parent))
from calibrate import WINDOWS, read_fit, resample, windows  # noqa: E402

BARO_THRESHOLDS = [10, 15, 20, 25, 30, 35]


class Dem:
    def __init__(self, folder: Path):
        files = [rasterio.open(p) for p in sorted(folder.rglob("*.tif"))]
        self.arr, self.tf = merge(files, nodata=np.nan)
        self.arr = self.arr[0].astype("float64")
        self.crs = files[0].crs
        self.to = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)

    def sample(self, lat, lon):
        x, y = self.to.transform(lon, lat)
        col, row = ~self.tf * (np.asarray(x), np.asarray(y))
        col, row = col - 0.5, row - 0.5             # pixel centres
        c0, r0 = np.floor(col).astype(int), np.floor(row).astype(int)
        fc, fr = col - c0, row - r0
        h, w = self.arr.shape
        ok = (c0 >= 0) & (r0 >= 0) & (c0 < w - 1) & (r0 < h - 1)
        c0, r0 = np.clip(c0, 0, w - 2), np.clip(r0, 0, h - 2)
        a = self.arr
        z = (a[r0, c0] * (1 - fc) * (1 - fr) + a[r0, c0 + 1] * fc * (1 - fr)
             + a[r0 + 1, c0] * (1 - fc) * fr + a[r0 + 1, c0 + 1] * fc * fr)
        z[~ok] = np.nan
        return z


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--dem", action="append", required=True, help="name=folder")
    ap.add_argument("--out", type=Path, default=Path("out/calibration"))
    a = ap.parse_args()
    dems = {k: Dem(Path(v)) for k, v in (d.split("=", 1) for d in a.dem)}

    rides = []
    for f in sorted(a.files):
        if f.stem.split("_")[0] in a.exclude:
            continue
        g = resample(read_fit(f))
        zs = {k: d.sample(g["lat"], g["lon"]) for k, d in dems.items()}
        if any(np.isnan(z).mean() > 0.01 for z in zs.values()):
            continue                                 # ride outside DEM coverage
        rides.append((f.stem.split("_")[0], g, zs))

    report = {"rides": [r[0] for r in rides], "elevation": {}, "gradient": {}}
    for k in dems:
        errs = []
        for _, g, zs in rides:
            e = zs[k] - g["alt"]
            errs.append(e - np.median(e))
        e = np.concatenate(errs)
        report["elevation"][k] = {"mad_m": round(float(np.median(np.abs(e))), 2),
                                  "p95_abs_m": round(float(np.percentile(np.abs(e), 95)), 2)}

    for L in WINDOWS:
        baro, ride_mask, dz = [], [], {k: [] for k in dems}
        for _, g, zs in rides:
            w = windows(g, L)
            n = int(L / 5.0)
            m = w["riding"] & (w["grad"] > 1.0)
            baro.append(w["grad"][m])
            for k in dems:
                gd = (zs[k][n:] - zs[k][:-n]) / L * 100.0
                dz[k].append(gd[m])
        b = np.concatenate(baro)
        entry = {"n_windows": int(len(b))}
        for k in dems:
            d = np.concatenate(dz[k])
            corr = float(np.corrcoef(b, d)[0, 1])
            # quantile mapping: which DEM gradient sits at the same rank as baro threshold T?
            mapping = {}
            for T in BARO_THRESHOLDS:
                q = float(np.mean(b < T)) * 100
                mapping[f"{T}%"] = None if q >= 99.99 else round(float(np.percentile(d, q)), 1)
            entry[k] = {"corr": round(corr, 3), "max_dem": round(float(d.max()), 1),
                        "max_baro": round(float(b.max()), 1),
                        "median_abs_diff": round(float(np.median(np.abs(d - b))), 1),
                        "baro_to_dem": mapping}
        report["gradient"][f"{L}m"] = entry

    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "dem_vs_baro.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
