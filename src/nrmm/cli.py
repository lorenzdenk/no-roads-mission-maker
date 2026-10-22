"""Kommandozeile: nrmm-cli route|sweep|info (gleiche Funktionen wie die Oberflaeche)."""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import router
from .export import parse_gpx, write_outputs
from .network import DEFAULT_EXCLUDED, FACTORS_V1, KEYS, build_network, factors_for_k
from .pbf import read_header_bbox, read_pbf


def _ll(s):
    la, lo = (float(x) for x in s.replace(";", ",").split(","))
    return la, lo


_last = [-1]


def _progress(p, m):
    step = int(p * 20)
    if step != _last[0] or p >= 1:
        _last[0] = step
        print(f"{m:<36} {p*100:5.1f} %", file=sys.stderr, flush=True)


def _load(a):
    cache = os.path.join(os.path.expanduser("~"), ".no-roads-mission-maker", "cache") if not a.no_cache else None
    bbox = tuple(float(x) for x in a.bbox.split(",")) if a.bbox else None
    d = read_pbf(a.pbf, bbox=bbox, progress=_progress, cache_dir=cache)
    return build_network(d, par_dist=a.par_dist, par_share=a.par_share)


def _factors(a):
    f = factors_for_k(a.k) if a.mode == "paths" else dict(FACTORS_V1)
    for item in a.factor or []:
        k, v = item.split("=")
        if k not in KEYS:
            raise SystemExit(f"Unbekannte Klasse '{k}'. Moeglich: {', '.join(KEYS)}")
        f[k] = None if v in ("x", "gesperrt") else float(v)
    return f


def main(argv=None):
    p = argparse.ArgumentParser(prog="nrmm-cli", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("route", "sweep"):
        s = sub.add_parser(name)
        s.add_argument("pbf")
        s.add_argument("--start", type=_ll, help="lat,lon")
        s.add_argument("--end", type=_ll, help="lat,lon")
        s.add_argument("--gpx", help="GPX-Track: Start/Ziel daraus, mit --corridor als Korridor")
        s.add_argument("--corridor", type=float, help="Korridorbreite in m um die GPX-Linie")
        s.add_argument("--mode", choices=["weighted", "paths"], default="weighted")
        s.add_argument("--k", type=float, default=5.0, help="Faktor K fuer --mode paths")
        s.add_argument("--factor", action="append", metavar="KLASSE=WERT", help="Faktor ueberschreiben, x = gesperrt")
        s.add_argument("--par-dist", type=float, default=10.0)
        s.add_argument("--par-share", type=float, default=0.5)
        s.add_argument("--bbox", help="min_lat,min_lon,max_lat,max_lon")
        s.add_argument("--no-cache", action="store_true")
        s.add_argument("--out", default="out")
        if name == "sweep":
            s.add_argument("--ks", default="1.5,2,3,5,8,12,20,40")
    i = sub.add_parser("info")
    i.add_argument("pbf")
    a = p.parse_args(argv)

    if a.cmd == "info":
        print(json.dumps(read_header_bbox(a.pbf), indent=1))
        return 0

    net = _load(a)
    gpx = parse_gpx(open(a.gpx, "rb").read()) if a.gpx else None
    start = a.start or (tuple(gpx[0]) if gpx is not None else None)
    end = a.end or (tuple(gpx[-1]) if gpx is not None else None)
    if start is None or end is None:
        p.error("Start und Ziel (--start/--end) oder --gpx angeben.")
    allowed = router.corridor_mask(net, gpx, a.corridor) if (gpx is not None and a.corridor) else None

    if a.cmd == "sweep":
        rows = router.sweep(net, start, end, [float(x) for x in a.ks.split(",")], allowed)
        print(f"{'':>10} {'km':>8} {'Pfad %':>8} {'Strasse %':>10} {'Umweg %':>8}")
        for r in rows:
            print(f"{r['label']:>10} {r['length_m']/1000:8.2f} {r['pfad_share']*100:8.1f} {r['strasse_share']*100:10.1f} {r['umweg']*100:8.1f}")
        return 0

    ref = router.shortest(net, start, end)
    r = router.solve(net, start, end, _factors(a), allowed)
    summary = router.summarize(net, r, ref)
    summary["kuerzeste"] = router.summarize(net, ref)
    res = dict(coords=r.coords(net), ref_coords=ref.coords(net), summary=summary, access=router.access_log(net, r))
    files = write_outputs(a.out, f"{a.mode}", res)
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    print("\n".join(files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
