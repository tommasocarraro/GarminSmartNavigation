"""Build the Garmin map: scored OSM + contours -> splitter -> mkgmap (style/gsn, DEM shading).

Outputs in out/<area>/:
  GSN_<area>.img     gmapsupp for the Edge (copy to GARMIN/Garmin/ on the device)
  img/<name>.gmap    gmapi bundle for BaseCamp (--install-basecamp copies it in place)

Usage: python -m gsn.mkmap --area colli_euganei [--install-basecamp]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
JAVA = Path.home() / "miniconda3/envs/gsn/lib/jvm/bin/java"
MKGMAP = ROOT / "vendor/mkgmap-r4924/mkgmap.jar"
SPLITTER = ROOT / "vendor/splitter-r654/splitter.jar"
BASECAMP_MAPS = Path.home() / "Library/Application Support/Garmin/Maps"


def run(cmd, log: Path):
    print("  $", " ".join(str(c) for c in cmd)[:200], "...")
    with open(log, "w") as f:
        r = subprocess.run([str(c) for c in cmd], stdout=f, stderr=subprocess.STDOUT)
    if r.returncode:
        print(log.read_text()[-3000:])
        raise SystemExit(f"failed ({r.returncode}), log: {log}")


def build(area: str, cfg: dict, install_basecamp: bool):
    mc = cfg["map"]
    out = ROOT / "out" / area
    work = out / "work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    java = str(JAVA) if JAVA.exists() else "java"

    # 1. sorted, merged input (split pieces have new ids, so the scored file needs sorting)
    scored = out / f"{area}_gsn.osm.pbf"
    sorted_pbf = work / "scored_sorted.osm.pbf"
    run(["osmium", "sort", "-O", "-o", sorted_pbf, scored], work / "sort.log")
    inputs = [sorted_pbf]
    contours = ROOT / "cache/terrain" / area / "contours.osm.pbf"
    if mc.get("contours") and contours.exists():
        inputs.append(contours)
    merged = work / "map.osm.pbf"
    run(["osmium", "merge", "-O", "-o", merged, *inputs], work / "merge.log")

    # 2. splitter -> tiles
    fam = mc["family_id"]
    tiles = work / "tiles"
    tiles.mkdir()
    run([java, "-Xmx4g", "-jar", SPLITTER, f"--mapid={fam}0001", "--max-nodes=1600000",
         f"--output-dir={tiles}", merged], work / "splitter.log")

    # 3. mkgmap
    img = out / "img"
    shutil.rmtree(img, ignore_errors=True)
    name = f"{mc['name']} {area}"
    args = [java, "-Xmx4g", "-jar", MKGMAP,
            f"--output-dir={img}",
            f"--style-file={ROOT / 'style'}", "--style=gsn", "--check-styles",
            "--route", "--drive-on=right",
            f"--family-id={fam}", f"--product-id={mc['product_id']}",
            f"--family-name={name}", f"--series-name={name}", f"--description={name}",
            f"--area-name={area}", f"--overview-mapname=gsn_{area}",
            "--code-page=1252", "--lower-case",
            "--add-pois-to-areas", "--show-profiles=1",
            f"--draw-priority={mc.get('draw_priority', 25)}",
            "--gmapsupp", "--gmapi"]
    hgt = ROOT / "cache/terrain" / area / "hgt"
    if mc.get("hill_shading") and hgt.exists():
        args += [f"--dem={hgt}", "--dem-dists=3312,13248,26512,53024"]
    args += ["-c", tiles / "template.args"]
    run(args, work / "mkgmap.log")

    dst = out / f"GSN_{area}.img"
    shutil.copy(img / "gmapsupp.img", dst)
    print(f"Edge map: {dst} ({dst.stat().st_size / 1e6:.1f} MB)")
    gmap = next(img.glob("*.gmap"))
    print(f"BaseCamp bundle: {gmap}")
    if install_basecamp:
        target = BASECAMP_MAPS / gmap.name
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(gmap, target)
        print(f"installed for BaseCamp: {target} (restart BaseCamp, then Maps menu)")
    warn = [l for l in (work / "mkgmap.log").read_text().splitlines() if "SEVERE" in l or "ERROR" in l]
    if warn:
        print(f"{len(warn)} SEVERE/ERROR lines in {work / 'mkgmap.log'}; first: {warn[0]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", required=True)
    ap.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    ap.add_argument("--install-basecamp", action="store_true")
    a = ap.parse_args()
    build(a.area, yaml.safe_load(a.config.read_text()), a.install_basecamp)


if __name__ == "__main__":
    main()
