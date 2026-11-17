"""Score every segment and direction, then write a modified OSM file with split ways.

Per direction (fwd = OSM node order, bwd = reverse) a segment collects penalty points from
  gradient (worst 50/100 m, sustained 200/500/1000 m along the way), harsher on rough ground,
  technical tags (mtb:scale, smoothness, surface, visibility, sac_scale, informal, abandoned),
  traffic (uphill traces shrink the uphill penalty; no traffic adds a little),
  route=mtb membership (bonus).
Hard blocks: a direction can be blocked (wall, rough+steep, mtb:scale >= block, DH trail,
downhill-only traffic) -> written as a oneway; or the whole way (steps, bicycle=no, ...).

Garmin can't treat the directions differently, so the segment value is the max of the
allowed directions. Output tags per (split) way:
  gsn:speed (0-7), gsn:class (0-4), gsn:oneway (yes / -1), gsn:bike=no,
  plus gsn:pts and gsn:why for debugging.

Usage: python -m gsn.score --area colli_euganei
"""
from __future__ import annotations

import argparse
import math
import pickle
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import osmium
import yaml

from gsn.segments import TRAIL, hav

SOFT_TRAILS = {"path", "footway", "bridleway"}
SUSTAINED = (200, 500, 1000)
UNPAVED = {"ground", "dirt", "gravel", "unpaved", "grass", "rock", "compacted", "fine_gravel", "earth", "mud",
           "stone", "pebblestone", "scree", "sand", "wood", "roots"}
NEW_ID = 10 ** 11            # ids for new nodes / ways created by splitting


# --------------------------------------------------------------------------- inputs
def mtb_relations(pbf: Path):
    """way id -> set of flags from route relations ('route', 'dh')."""
    flags = defaultdict(set)
    for o in osmium.FileProcessor(str(pbf), osmium.osm.RELATION):
        t = dict(o.tags)
        if t.get("route") != "mtb":
            continue
        dh = t.get("mtb:type") == "downhill" or t.get("piste:type") == "downhill"
        for m in o.members:
            if m.type == "w":
                flags[m.ref].add("route")
                if dh:
                    flags[m.ref].add("dh")
    return flags


def way_strokes(pbf: Path, way_ids, max_deflect=45.0):
    """Chain ways that continue each other into 'strokes' (a climb split into several OSM ways).

    At every node where way ends meet, the two ends that continue straightest (deflection
    < max_deflect degrees) are joined. Returns a list of strokes, each a list of (way, reversed).
    """
    ends = defaultdict(list)            # node -> [(way, at_start, departure bearing)]
    for o in osmium.FileProcessor(str(pbf), osmium.osm.NODE | osmium.osm.WAY).with_locations():
        if not o.is_way() or o.id not in way_ids:
            continue
        try:
            ll = [(n.lat, n.lon) for n in o.nodes]
        except osmium.InvalidLocationError:
            continue
        refs = [n.ref for n in o.nodes]
        if len(refs) < 2 or refs[0] == refs[-1]:
            continue

        def bearing(p, q):
            return math.degrees(math.atan2((q[1] - p[1]) * math.cos(math.radians(p[0])), q[0] - p[0]))

        def ahead(seq):                  # a point ~20 m into the way (or its far end)
            d = 0.0
            for k in range(1, len(seq)):
                d += hav(seq[k - 1][0], seq[k - 1][1], seq[k][0], seq[k][1])
                if d >= 20:
                    return seq[k]
            return seq[-1]
        ends[refs[0]].append((o.id, True, bearing(ll[0], ahead(ll))))
        ends[refs[-1]].append((o.id, False, bearing(ll[-1], ahead(ll[::-1]))))
    link = {}                            # (way, at_start) -> (other way, other at_start)
    for node, lst in ends.items():
        cand = []
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                if lst[i][0] == lst[j][0]:
                    continue
                diff = abs((lst[i][2] - lst[j][2] + 180) % 360 - 180)
                cand.append((180 - diff, i, j))
        used = set()
        for dfl, i, j in sorted(cand):
            if dfl > max_deflect:
                break
            if i in used or j in used:
                continue
            used |= {i, j}
            a_, b_ = lst[i], lst[j]
            link[(a_[0], a_[1])] = (b_[0], b_[1])
            link[(b_[0], b_[1])] = (a_[0], a_[1])
    strokes, seen = [], set()
    all_ways = {w for lst in ends.values() for w, _, _ in lst}

    def walk(w, rev):
        """Follow links from way w traversed with orientation rev; returns list after w."""
        out = []
        while True:
            exit_end = (w, rev)          # leaving w at its start if reversed, else at its end
            nxt = link.get((w, True) if rev else (w, False))
            if not nxt or nxt[0] in seen:
                return out
            w2, at_start = nxt
            rev = not at_start           # entering at its start -> traversed forward
            seen.add(w2)
            out.append((w2, rev))
            w = w2
    for w in all_ways:
        if w in seen:
            continue
        seen.add(w)
        fwd = walk(w, False)
        back = walk(w, True)             # going the other way from w's start
        stroke = [(x, not r) for x, r in back[::-1]] + [(w, False)] + fwd
        strokes.append(stroke)
    return strokes


def add_sustained(segs, strokes=None):
    """Worst sustained uphill gradient over 200/500/1000 m windows, per direction, measured along
    strokes (chains of ways that continue each other) so climbs split into several ways count whole."""
    by_way = defaultdict(list)
    for s in segs:
        by_way[s["way"]].append(s)
    for lst in by_way.values():
        lst.sort(key=lambda s: s["id"])
    if strokes is None:
        strokes = [[(w, False)] for w in by_way]
    covered = {w for st in strokes for w, _ in st}
    strokes = strokes + [[(w, False)] for w in by_way if w not in covered]
    for s in segs:
        for W in SUSTAINED:
            s[f"sus{W}_fwd"] = s[f"sus{W}_bwd"] = 0.0
    for stroke in strokes:
        seq = []                          # (segment, sign): sign -1 when the way runs backwards
        for w, rev in stroke:
            pcs = by_way.get(w, [])
            seq += [(x, -1) for x in pcs[::-1]] if rev else [(x, 1) for x in pcs]
        if not seq:
            continue
        L = np.array([x["len_m"] for x, _ in seq])
        S = np.r_[0, np.cumsum(L)]
        Z = np.r_[0, np.cumsum([x["grad_avg"] * sg / 100 * x["len_m"] for x, sg in seq])]
        for W in SUSTAINED:
            if S[-1] < W:
                continue
            st = np.arange(0, S[-1] - W + 1e-6, 10.0)
            g = (np.interp(st + W, S, Z) - np.interp(st, S, Z)) / W * 100
            for i, (x, sg) in enumerate(seq):
                m = (st + W > S[i]) & (st < S[i + 1])
                if m.any():
                    up, dn = float(max(g[m].max(), 0)), float(max((-g[m]).max(), 0))
                    f, b = ("fwd", "bwd") if sg > 0 else ("bwd", "fwd")
                    x[f"sus{W}_{f}"] = max(x[f"sus{W}_{f}"], up)
                    x[f"sus{W}_{b}"] = max(x[f"sus{W}_{b}"], dn)


def add_way_traffic(segs, cfg):
    """Per way: its overall uphill direction and how many traces rode it up / down (max over pieces)."""
    tr = cfg["traffic"]
    by_way = defaultdict(list)
    for s in segs:
        by_way[s["way"]].append(s)
    for lst in by_way.values():
        L = sum(s["len_m"] for s in lst)
        avg = sum(s["grad_avg"] * s["len_m"] for s in lst) / max(L, 1)
        info = None
        if L >= tr["downhill_only_min_len"] and abs(avg) >= tr["downhill_only_min_grade"]:
            up = "fwd" if avg > 0 else "bwd"
            dn = "bwd" if up == "fwd" else "fwd"
            info = dict(up=up, n_up=max(s.get(f"n_{up}", 0) for s in lst),
                        n_dn=max(s.get(f"n_{dn}", 0) for s in lst))
        for s in lst:
            s["way_traffic"] = info


# --------------------------------------------------------------------------- scoring
def level(value, limits):
    return sum(value > x for x in limits)


def tag_num(t, k):
    v = t.get(k)
    if v is None:
        return None
    try:
        return float(str(v).rstrip("+-").split(";")[0])
    except ValueError:
        return None


def base_of(t, sc):
    hw = t.get("highway")
    if hw == "track":
        key = "track_good" if t.get("tracktype") in ("grade1", "grade2", "grade3") else "track"
    else:
        key = hw
    return sc["base"].get(key, [3, 0])


def score_segment(s, rel, cfg):
    sc, gr, tech, tr = cfg["scoring"], cfg["gradient"], cfg["technical"], cfg["traffic"]
    t, hw = s["tags"], s["highway"]
    trail = hw in TRAIL
    flags = rel.get(s["way"], set())
    why = []

    # ---- whole-way blocks
    bike_ok = t.get("bicycle") in ("yes", "designated", "permissive", "official")
    for k, vals in sc["block_access_tags"].items():
        if t.get(k) in vals and not (k == "access" and bike_ok):
            return dict(block="both", why=f"{k}={t.get(k)}")
    if t.get("sac_scale") in tech["sac_scale_block"]:
        return dict(block="both", why=f"sac_scale={t['sac_scale']}")

    # ---- direction-independent features
    mtb = tag_num(t, "mtb:scale")
    mtb_up_tag = tag_num(t, "mtb:scale:uphill")
    bad_surf = t.get("surface") in tech["bad_surface"]
    bad_smooth = t.get("smoothness") in tech["bad_smoothness"]
    has_mtb = mtb is not None or mtb_up_tag is not None
    tech_tagged = has_mtb or "smoothness" in t or "surface" in t
    m_up = mtb_up_tag if mtb_up_tag is not None else mtb
    strong_rough = bad_surf or (m_up or 0) >= tech["mtb_scale_uphill_rough"] or (bad_smooth and not has_mtb)
    rough = strong_rough or (trail and not tech_tagged and s["rough"] > sc["dem_rough_m"])
    dh_trail = t.get("mtb:type") == "downhill" or "dh" in flags

    pts0 = 0.0
    if not has_mtb:     # mtb:scale already describes the ground better for bikes
        pts0 += sc["smoothness"].get(t.get("smoothness"), 0)
    pts0 += sc["surface"].get(t.get("surface"), 0)
    pts0 += sc["visibility"].get(t.get("trail_visibility"), 0)
    pts0 += sc["sac_scale"].get(t.get("sac_scale"), 0)
    if t.get("informal") == "yes":
        pts0 += sc["informal"]
    if t.get("abandoned") == "yes" or t.get("disused") == "yes":
        pts0 += sc["abandoned"]
    if mtb is not None:
        pts0 += sc["mtb_scale_any_dir"].get(int(min(mtb, 6)), 0)
    if pts0:
        why.append(f"tags+{pts0:g}")

    res = {}
    for d, o in (("fwd", "bwd"), ("bwd", "fwd")):
        sign = 1 if d == "fwd" else -1
        avg = s["grad_avg"] * sign
        w100 = s[f"worst100_{d}"]
        uphill = avg >= sc["uphill_grade_avg"] or w100 >= 2 * sc["uphill_grade_avg"]
        n_up, n_dn = s.get(f"n_{d}", 0), s.get(f"n_{o}", 0)
        v_up = s.get(f"v_{d}")
        w = n_up / (n_up + tr["k"]) if n_up else 0.0
        if w and v_up is not None and v_up < tr["walk_kmh"]:
            w /= 2
        climbed = n_up >= tr["climbed_overrides_block"]
        dwhy = []

        # gradient levels
        scale = sc["rough_grade_scale"] if rough else 1.0
        vals = {50: s[f"worst50_{d}"], 100: w100}
        for W in SUSTAINED:
            vals[W] = s.get(f"sus{W}_{d}", 0.0)
        lv = {W: level(v, [x * scale for x in gr[W]]) for W, v in vals.items()}
        L = max(lv.values())
        g_pts = sc["gradient_points"][L]
        if g_pts:
            dwhy.append(f"grad{max(lv, key=lv.get)}:{L}{'r' if rough else ''}")

        # uphill technical
        u_pts = 0.0
        if uphill:
            if m_up is not None and m_up > tech["mtb_scale_uphill_ok"]:
                u_pts = sc["mtb_uphill_points"].get(int(min(m_up, 6)), 0)
                dwhy.append(f"mtbup{m_up:g}")

        # hard blocks of this direction
        block = None
        raw = {W: level(v, gr[W]) for W, v in vals.items()}    # walls: unscaled limits, any ground
        if raw[50] == 3 or raw[100] == 3:
            block = "wall"
        if raw[200] == 3 or raw[500] == 3:
            block = "sustained wall"
        if strong_rough and w100 > gr["rough_steep_block_100m"]:
            block = "rough+steep"
        if uphill and mtb is not None and mtb >= tech["mtb_scale_block_uphill"] and \
                mtb_up_tag is None:
            block = f"mtb:scale={mtb:g}"
        if uphill and mtb_up_tag is not None and mtb_up_tag >= tech["mtb_scale_uphill_block"]:
            block = f"mtb:scale:uphill={mtb_up_tag:g}"
        if climbed and block:
            dwhy.append(f"unblocked({block},n={n_up})")
            block = None
        if uphill and dh_trail:
            block = "DH trail"      # traces never unblock a DH line
        wt = s.get("way_traffic")
        dh_pts = 0.0
        if wt and wt["up"] == d and wt["n_up"] == 0 and wt["n_dn"] > 0:
            tech_trail = (m_up or 0) >= 2 or (mtb or 0) >= 2
            if wt["n_dn"] >= tr["min_traces_for_downhill_only_block"] or (
                    tech_trail and wt["n_dn"] >= tr["min_traces_for_downhill_only_block_tech"]):
                block = f"downhill-only traffic(n={wt['n_dn']})"
            else:
                dh_pts = tr["downhill_only_points"] * wt["n_dn"] / (wt["n_dn"] + tr["k"])
                dwhy.append(f"downhill-only{wt['n_dn']}")

        pts = (g_pts + u_pts + pts0) * (1 - w)     # others climb it -> it is doable
        pts += dh_pts
        if w and (g_pts or u_pts or pts0):
            dwhy.append(f"climbed{n_up}")
        res[d] = dict(pts=pts, block=block, why=dwhy, uphill=uphill)

    # ---- combine directions
    if res["fwd"]["block"] and res["bwd"]["block"]:
        # both directions blocked on gradient/tech: keep it routable but at the very bottom
        return dict(block=None, oneway=None, pts=99, why=";".join(why + ["both-dir:" + res["fwd"]["block"]]))
    oneway = None
    if res["fwd"]["block"]:
        oneway, allowed = "-1", ["bwd"]
        why.append(f"block-fwd:{res['fwd']['block']}")
    elif res["bwd"]["block"]:
        oneway, allowed = "yes", ["fwd"]
        why.append(f"block-bwd:{res['bwd']['block']}")
    else:
        allowed = ["fwd", "bwd"]
    pts = max(res[d]["pts"] for d in allowed)
    for d in allowed:
        why += res[d]["why"]
    if hw in SOFT_TRAILS and s.get("n_fwd", 0) + s.get("n_bwd", 0) == 0:
        pts += sc["no_traffic"]
        why.append("no-traffic")
    if t.get("bicycle") == "dismount":       # a rule, not a rideability question: traces don't lift it
        pts += sc["dismount"]
        why.append("dismount")
    if "route" in flags:
        pts -= sc["mtb_route_bonus"]
        why.append("mtb-route")
    return dict(block=None, oneway=oneway, pts=pts, why=";".join(why))


def finalize(s, r, cfg):
    sc = cfg["scoring"]
    t = s["tags"]
    spd0, cls0 = base_of(t, sc)
    if r.get("block") == "both":
        return dict(speed=0, cls=0, oneway=None, bike="no", pts=None, why=r["why"])
    p = r["pts"]
    speed = int(np.clip(spd0 - math.floor(p + 0.5), 0, max(spd0, sc["max_speed"])))
    if p >= sc["zero_speed_points"]:
        speed = 0
    cls = cls0
    if s["highway"] in TRAIL and speed >= sc["trail_class_min_speed"]:
        cls = max(cls, 1)
    if speed <= sc["low_speed_class0"]:
        cls = 0
    bike = None
    # mkgmap's default style keeps bikes off footway/bridleway; open the ones that are mountain trails
    # (urban sidewalks stay pedestrian, as Italian rules say)
    if s["highway"] in ("footway", "bridleway") and "bicycle" not in t and (
            any(k in t for k in ("sac_scale", "mtb:scale", "trail_visibility")) or t.get("surface") in UNPAVED):
        bike = "yes"
    return dict(speed=speed, cls=cls, oneway=r.get("oneway"), bike=bike, pts=round(p, 2), why=r["why"])


# --------------------------------------------------------------------------- writing
def out_tags(f):
    tg = {"gsn:speed": str(f["speed"]), "gsn:class": str(f["cls"])}
    if f["oneway"]:
        tg["gsn:oneway"] = f["oneway"]
    if f["bike"]:
        tg["gsn:bike"] = f["bike"]
    if f["pts"] is not None:
        tg["gsn:pts"] = f"{f['pts']:g}"
    if f["why"]:
        tg["gsn:why"] = f["why"][:250]
    return tg


def write_osm(src: Path, dst: Path, segs, final):
    """Copy src to dst, splitting scored ways where the output tags change."""
    by_way = defaultdict(list)
    for s in segs:
        by_way[s["way"]].append(s)
    for lst in by_way.values():
        lst.sort(key=lambda s: s["id"])

    # pass 1: plan the splits (needs node locations)
    plans, new_nodes = {}, []
    next_node = [NEW_ID]
    for o in osmium.FileProcessor(str(src), osmium.osm.NODE | osmium.osm.WAY).with_locations():
        if not o.is_way() or o.id not in by_way:
            continue
        refs = [n.ref for n in o.nodes]
        ll = np.array([(n.lat, n.lon) for n in o.nodes])
        cum = np.r_[0, np.cumsum(hav(ll[:-1, 0], ll[:-1, 1], ll[1:, 0], ll[1:, 1]))]
        lst = by_way[o.id]
        # group consecutive pieces with identical output
        groups, pos = [], 0.0
        for s in by_way[o.id]:
            tg = out_tags(final[s["id"]])
            key = tuple(sorted((k, v) for k, v in tg.items() if k not in ("gsn:pts", "gsn:why")))
            if groups and groups[-1][0] == key:
                groups[-1][2] = tg if (tg.get("gsn:pts") or "") > (groups[-1][2].get("gsn:pts") or "") else groups[-1][2]
            else:
                groups.append([key, pos, tg])
            pos += s["len_m"]
        if len(groups) == 1:
            plans[o.id] = [(refs, groups[0][2])]
            continue
        scale = cum[-1] / pos if pos > 0 else 1.0
        seq = list(zip(cum, refs))                    # (distance, ref) incl. inserted nodes
        cut_refs = []
        for g in groups[1:]:
            cut = g[1] * scale
            j = int(np.argmin(np.abs(cum - cut)))
            if abs(cum[j] - cut) < 10 and 0 < j < len(refs) - 1:
                cut_refs.append(refs[j])
            else:
                k = min(max(int(np.searchsorted(cum, cut)) - 1, 0), len(refs) - 2)
                f = (cut - cum[k]) / max(cum[k + 1] - cum[k], 1e-9)
                nid = next_node[0]
                next_node[0] += 1
                new_nodes.append((nid, ll[k, 0] + f * (ll[k + 1, 0] - ll[k, 0]),
                                  ll[k, 1] + f * (ll[k + 1, 1] - ll[k, 1])))
                seq.append((cut, nid))
                cut_refs.append(nid)
        seq.sort(key=lambda x: x[0])
        # cuts: (ref, index of the group that starts there); a repeated ref means an empty group
        cuts = []
        for gi, r in enumerate(cut_refs, start=1):
            if cuts and cuts[-1][0] == r:
                cuts[-1] = (r, gi)
            else:
                cuts.append((r, gi))
        pieces, cur, ci, gcur = [], [seq[0][1]], 0, 0
        for _, r in seq[1:]:
            cur.append(r)
            if ci < len(cuts) and r == cuts[ci][0]:
                pieces.append((cur, groups[gcur][2]))
                cur, gcur = [r], cuts[ci][1]
                ci += 1
        pieces.append((cur, groups[gcur][2]))
        plans[o.id] = [p for p in pieces if len(p[0]) >= 2]

    # pass 2: write
    next_way = NEW_ID
    way_pieces = {}
    if dst.exists():
        dst.unlink()
    wr = osmium.SimpleWriter(str(dst))
    nodes_done = False
    rel_cache = []
    try:
        for o in osmium.FileProcessor(str(src)):
            if o.is_node():
                wr.add_node(o)
                continue
            if not nodes_done:
                for nid, lat, lon in new_nodes:
                    wr.add_node(osmium.osm.mutable.Node(id=nid, location=(lon, lat), version=1, tags={}))
                nodes_done = True
            if o.is_way():
                if o.id not in plans:
                    wr.add_way(o)
                    continue
                base = dict(o.tags)
                ids = []
                for k, (refs, tg) in enumerate(plans[o.id]):
                    wid = o.id if k == 0 else next_way
                    if k:
                        next_way += 1
                    ids.append((wid, refs))
                    wr.add_way(osmium.osm.mutable.Way(id=wid, nodes=refs, tags={**base, **tg}, version=1))
                way_pieces[o.id] = ids
            elif o.is_relation():
                wr.add_relation(fix_relation(o, way_pieces))
    finally:
        wr.close()
    return len(new_nodes), next_way - NEW_ID


def fix_relation(o, way_pieces):
    t = dict(o.tags)
    mem = [(m.type, m.ref, m.role) for m in o.members]
    if not any(m[0] == "w" and m[1] in way_pieces and len(way_pieces[m[1]]) > 1 for m in mem):
        return o
    out = []
    if t.get("type") == "restriction":
        via = {m[1] for m in mem if m[0] == "n" and m[2] == "via"}
        for ty, ref, role in mem:
            if ty == "w" and role == "via" and ref in way_pieces:
                for wid, refs in way_pieces[ref]:
                    via |= {refs[0], refs[-1]}
        for ty, ref, role in mem:
            if ty == "w" and ref in way_pieces and len(way_pieces[ref]) > 1:
                pick = [wid for wid, refs in way_pieces[ref] if via & set(refs)] or [way_pieces[ref][0][0]]
                out += [("w", wid, role) for wid in (pick if role == "via" else pick[:1])]
            else:
                out.append((ty, ref, role))
    else:
        for ty, ref, role in mem:
            if ty == "w" and ref in way_pieces:
                out += [("w", wid, role) for wid, _ in way_pieces[ref]]
            else:
                out.append((ty, ref, role))
    return osmium.osm.mutable.Relation(id=o.id, members=out, tags=t, version=1)


# --------------------------------------------------------------------------- main
def report(segs, final):
    def km(sel):
        return sum(s["len_m"] for s in sel) / 1000
    trail = [s for s in segs if s["highway"] in TRAIL]
    print(f"Trails {km(trail):.0f} km — road_speed distribution:")
    for v in range(0, 6):
        sel = [s for s in trail if final[s["id"]]["speed"] == v and final[s["id"]]["bike"] != "no"]
        print(f"  speed {v}: {km(sel):6.1f} km")
    bl = [s for s in segs if final[s["id"]]["bike"] == "no"]
    ow = [s for s in segs if final[s["id"]]["oneway"]]
    print(f"No bikes (both directions), all ways: {km(bl):.1f} km")
    print(f"One-way (uphill blocked), all ways: {km(ow):.1f} km")
    c = Counter()
    for s in segs:
        f = final[s["id"]]
        if f["bike"] == "no":
            c["no bikes: " + f["why"]] += s["len_m"] / 1000
        for part in (f["why"] or "").split(";"):
            if part.startswith("block-"):
                c["uphill block: " + part.split(":", 1)[1].split("=")[0]] += s["len_m"] / 1000
            elif part.startswith("unblocked"):
                c["block lifted by traces: " + part[10:].split(",")[0].split("=")[0]] += s["len_m"] / 1000
    for k, v in c.most_common():
        print(f"  {k:40s} {v:6.1f} km")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", required=True)
    ap.add_argument("--config", type=Path, default=Path("config.yaml"))
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    cfg = yaml.safe_load(a.config.read_text())
    pbf = Path("cache/areas") / f"{a.area}.osm.pbf"
    segs = pickle.load(open(f"cache/segments/{a.area}.pkl", "rb"))
    strokes = way_strokes(pbf, {s["way"] for s in segs})
    add_sustained(segs, strokes)
    print(f"{len(strokes)} strokes from {len({s['way'] for s in segs})} ways "
          f"(longest {max(len(x) for x in strokes)} ways)")
    add_way_traffic(segs, cfg)
    rel = mtb_relations(pbf)
    final = {}
    for s in segs:
        final[s["id"]] = finalize(s, score_segment(s, rel, cfg), cfg)
    out = Path("cache/scored")
    out.mkdir(parents=True, exist_ok=True)
    with open(out / f"{a.area}.pkl", "wb") as f:
        pickle.dump({"segs": segs, "final": final}, f)
    report(segs, final)
    if not a.no_write:
        dst = Path("out") / a.area / f"{a.area}_gsn.osm.pbf"
        dst.parent.mkdir(parents=True, exist_ok=True)
        nn, nw = write_osm(pbf, dst, segs, final)
        print(f"wrote {dst}: {nn} new nodes, {nw} new ways from splits")


if __name__ == "__main__":
    main()
