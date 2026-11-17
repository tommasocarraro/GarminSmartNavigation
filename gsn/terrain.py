"""Terrain layers for the Garmin map, from the same DEM used for scoring.

1. Hill shading: 1 arc-second .hgt tiles (what mkgmap --dem reads), resampled from the DEM.
   Cells outside the DEM or the area get the hgt void value.
2. Contours: an OSM file of contour lines (contour=elevation, ele=<m>,
   contour_ext=elevation_major every `major` m, else elevation_minor), drawn by the
   style's inc/contour_lines. The DEM is lightly smoothed first so 10 m noise
   doesn't make wiggly lines.

Usage: python -m gsn.terrain --area colli_euganei
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import osmium
import rasterio
import yaml
from pyproj import Transformer
from scipy.ndimage import gaussian_filter
from shapely.geometry import LineString

from gsn.dem import Dem

HGT_N = 3601
VOID = -32768
CONTOUR_ID = 3 * 10 ** 11       # id range for contour nodes/ways (no clash with OSM or split pieces)


def write_hgt(dem: Dem, bbox, out: Path, pad_deg=0.01):
    out.mkdir(parents=True, exist_ok=True)
    l, b, r, t = bbox
    tiles = []
    for lat0 in range(math.floor(b), math.ceil(t)):
        for lon0 in range(math.floor(l), math.ceil(r)):
            lat = lat0 + 1 - np.arange(HGT_N) / (HGT_N - 1)       # north -> south rows
            lon = lon0 + np.arange(HGT_N) / (HGT_N - 1)
            z = np.full((HGT_N, HGT_N), VOID, dtype=">i2")
            rows = (lat >= b - pad_deg) & (lat <= t + pad_deg)
            cols = (lon >= l - pad_deg) & (lon <= r + pad_deg)
            if rows.any() and cols.any():
                LO, LA = np.meshgrid(lon[cols], lat[rows])
                v = dem.sample(LA.ravel(), LO.ravel()).reshape(LA.shape)
                sub = np.where(np.isnan(v), VOID, np.round(v)).astype(">i2")
                z[np.ix_(rows, cols)] = sub
            name = f"{'N' if lat0 >= 0 else 'S'}{abs(lat0):02d}{'E' if lon0 >= 0 else 'W'}{abs(lon0):03d}.hgt"
            (out / name).write_bytes(z.tobytes())
            tiles.append(name)
    return tiles


def write_contours(dem: Dem, bbox, out_pbf: Path, interval=20, major=100, sigma_px=1.5,
                   simplify_m=3.0, min_len_m=60.0):
    arr = gaussian_filter(np.nan_to_num(dem.arr, nan=-9999.0), sigma_px)
    arr[np.isnan(dem.arr)] = -9999.0
    with tempfile.TemporaryDirectory() as td:
        tif, gj = Path(td) / "dem.tif", Path(td) / "c.geojson"
        with rasterio.open(tif, "w", driver="GTiff", width=arr.shape[1], height=arr.shape[0], count=1,
                           dtype="float32", crs=dem.crs, transform=dem.tf, nodata=-9999.0) as ds:
            ds.write(arr.astype("float32"), 1)
        subprocess.run(["gdal_contour", "-q", "-a", "ele", "-i", str(interval), "-f", "GeoJSON",
                        str(tif), str(gj)], check=True)
        feats = json.loads(gj.read_text())["features"]
    to_ll = Transformer.from_crs(dem.crs, "EPSG:4326", always_xy=True)
    l, b, r, t = bbox
    nid = wid = CONTOUR_ID
    n_lines = 0
    if out_pbf.exists():
        out_pbf.unlink()
    nodes_out, ways_out = [], []
    for f in feats:
        ele = int(round(f["properties"]["ele"]))
        g = f["geometry"]
        parts = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        for coords in parts:
            ls = LineString(coords)
            if ls.length < min_len_m:
                continue
            ls = ls.simplify(simplify_m)
            x, y = np.array(ls.coords).T
            lon, lat = to_ll.transform(x, y)
            inside = (lon >= l) & (lon <= r) & (lat >= b) & (lat <= t)
            if not inside.any():
                continue
            refs = []
            for la, lo in zip(lat, lon):
                nodes_out.append((nid, la, lo))
                refs.append(nid)
                nid += 1
            # closed rings: reuse the first node
            if np.hypot(x[0] - x[-1], y[0] - y[-1]) < 0.01 and len(refs) > 3:
                nodes_out.pop()
                refs[-1] = refs[0]
            ways_out.append((wid, refs, {
                "contour": "elevation", "ele": str(ele),
                "contour_ext": "elevation_major" if ele % major == 0 else "elevation_minor"}))
            wid += 1
            n_lines += 1
    wr = osmium.SimpleWriter(str(out_pbf))
    try:
        for i, la, lo in nodes_out:
            wr.add_node(osmium.osm.mutable.Node(id=i, location=(lo, la), version=1, tags={}))
        for i, refs, tags in ways_out:
            wr.add_way(osmium.osm.mutable.Way(id=i, nodes=refs, tags=tags, version=1))
    finally:
        wr.close()
    return n_lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", required=True)
    ap.add_argument("--config", type=Path, default=Path("config.yaml"))
    a = ap.parse_args()
    cfg = yaml.safe_load(a.config.read_text())
    bbox = cfg["areas"][a.area]["bbox"]
    mc = cfg["map"]
    dem = Dem(Path("cache/dem") / mc.get("dem_for_shading", cfg["sources"]["dem"]), bbox)
    out = Path("cache/terrain") / a.area
    if mc.get("hill_shading", True):
        tiles = write_hgt(dem, bbox, out / "hgt")
        print(f"hill shading: {len(tiles)} hgt tile(s) in {out / 'hgt'}")
    if mc.get("contours", True):
        n = write_contours(dem, bbox, out / "contours.osm.pbf", interval=mc["contour_interval_m"],
                           major=mc.get("contour_major_m", 100))
        print(f"contours: {n} lines -> {out / 'contours.osm.pbf'}")


if __name__ == "__main__":
    main()
