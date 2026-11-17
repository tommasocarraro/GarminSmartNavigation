"""DEM access: merge GeoTIFF tiles from a folder and sample bilinearly at lat/lon."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.merge import merge


class Dem:
    def __init__(self, folder: Path | str, bbox: tuple[float, float, float, float] | None = None):
        files = [rasterio.open(p) for p in sorted(Path(folder).rglob("*.tif"))]
        if not files:
            raise FileNotFoundError(f"no DEM tiles in {folder}")
        self.crs = files[0].crs
        bounds = None
        if bbox is not None:
            to = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)
            xs, ys = to.transform([bbox[0], bbox[2], bbox[0], bbox[2]], [bbox[1], bbox[1], bbox[3], bbox[3]])
            pad = 200
            bounds = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
            files = [f for f in files if not (f.bounds.right < bounds[0] or f.bounds.left > bounds[2]
                                              or f.bounds.top < bounds[1] or f.bounds.bottom > bounds[3])]
        arr, self.tf = merge(files, bounds=bounds, nodata=np.nan)
        self.arr = arr[0].astype("float64")
        nod = files[0].nodata
        if nod is not None:
            self.arr[self.arr == nod] = np.nan
        self.to = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)

    def sample(self, lat, lon) -> np.ndarray:
        x, y = self.to.transform(np.asarray(lon, float), np.asarray(lat, float))
        col, row = ~self.tf * (np.asarray(x), np.asarray(y))
        col, row = np.asarray(col) - 0.5, np.asarray(row) - 0.5
        c0, r0 = np.floor(col).astype(int), np.floor(row).astype(int)
        fc, fr = col - c0, row - r0
        h, w = self.arr.shape
        ok = (c0 >= 0) & (r0 >= 0) & (c0 < w - 1) & (r0 < h - 1)
        c0, r0 = np.clip(c0, 0, w - 2), np.clip(r0, 0, h - 2)
        a = self.arr
        z = (a[r0, c0] * (1 - fc) * (1 - fr) + a[r0, c0 + 1] * fc * (1 - fr)
             + a[r0 + 1, c0] * (1 - fc) * fr + a[r0 + 1, c0 + 1] * fc * fr)
        return np.where(ok, z, np.nan)
