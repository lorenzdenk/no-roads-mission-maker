"""Lokaler Server fuer die Oberflaeche: JSON-API + statische Dateien.

Hoert nur auf 127.0.0.1, verlangt ein Sitzungs-Token und prueft den Host-Header.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

from . import __version__, router
from .export import parse_gpx, write_outputs
from .network import (CLASSES, DEFAULT_EXCLUDED, FACTORS_V1, K_MULT, KEYS, KIDX, build_network, factors_for_k)
from .pbf import Cancelled, read_header_bbox, read_pbf

_HOME = os.environ.get("NRMM_HOME") or os.path.join(os.path.expanduser("~"), ".no-roads-mission-maker")
CACHE_DIR = os.path.join(_HOME, "cache")
DEFAULT_OUT = os.environ.get("NRMM_OUT") or os.path.join(os.path.expanduser("~"), "Documents", "NoRoadsMissionMaker")
# Zeichenreihenfolge bei zu vielen Segmenten: wichtige Pfadklassen zuerst
DRAW_PRIORITY = ["pfad", "feldweg", "radweg", "fussg", "primary", "secondary", "nebenstr",
                 "pfad_par", "radweg_par", "service", "sidewalk", "quer"]


def web_dir() -> Path:
    here = Path(__file__).parent / "web"
    if here.exists():
        return here
    mp = getattr(sys, "_MEIPASS", None)
    if mp and (Path(mp) / "nrmm" / "web").exists():
        return Path(mp) / "nrmm" / "web"
    return here


class Job:
    def __init__(self, kind):
        self.id = secrets.token_hex(4)
        self.kind = kind
        self.state = "running"
        self.progress = 0.0
        self.message = ""
        self.error = None
        self.result = None
        self.cancel = False


class App:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = None
        self.net = None
        self.excluded = DEFAULT_EXCLUDED
        self.mid = None
        self.job = Job("idle")
        self.job.state = "idle"
        self.last = None
        self.picker = None  # wird von app.py gesetzt (pywebview) oder Tkinter
        self.token = secrets.token_urlsafe(16)
        self.port = 0

    # ------------------------------------------------------------- Jobs
    def start_job(self, kind, fn):
        with self.lock:
            if self.job.state == "running":
                raise RuntimeError("Es läuft bereits eine Berechnung.")
            job = Job(kind)
            self.job = job

        def run():
            try:
                job.result = fn(job)
                job.state = "done" if not job.cancel else "cancelled"
                job.progress = 1.0
            except Cancelled:
                job.state = "cancelled"
            except router.RouteError as e:
                job.state, job.error = "error", str(e)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                job.state, job.error = "error", f"{type(e).__name__}: {e}"

        threading.Thread(target=run, daemon=True).start()
        return job

    @staticmethod
    def _prog(job):
        def cb(p, m):
            if job.cancel:
                raise Cancelled()
            job.progress, job.message = float(p), m
        return cb

    # ------------------------------------------------------------- Laden
    def set_network(self, net):
        self.net = net
        mid_lat = (net.lat[net.a] + net.lat[net.b]) / 2
        mid_lon = (net.lon[net.a] + net.lon[net.b]) / 2
        self.mid = (mid_lat, mid_lon)
        self.last = None

    def do_load(self, job, body):
        path = body["path"]
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Datei nicht gefunden: {path}")
        bbox = tuple(body["bbox"]) if body.get("bbox") else None
        cb = self._prog(job)
        self.excluded = tuple(body.get("excluded") or DEFAULT_EXCLUDED)
        data = read_pbf(path, bbox=bbox, progress=lambda p, m: cb(p * 0.8, m), cache_dir=CACHE_DIR)
        self.data = data
        net = build_network(data, self.excluded, float(body.get("par_dist", 10)), float(body.get("par_share", 0.5)),
                            progress=lambda p, m: cb(0.8 + p * 0.2, m))
        self.set_network(net)
        return self.state()

    def do_rebuild(self, job, body):
        if self.data is None:
            raise RuntimeError("Lade zuerst eine Kartendatei.")
        cb = self._prog(job)
        self.excluded = tuple(body.get("excluded") or DEFAULT_EXCLUDED)
        net = build_network(self.data, self.excluded, float(body.get("par_dist", 10)), float(body.get("par_share", 0.5)), progress=cb)
        self.set_network(net)
        return self.state()

    def state(self):
        n = self.net
        if n is None:
            return dict(loaded=False, version=__version__, out_dir=DEFAULT_OUT)
        b = n.bounds()
        return dict(loaded=True, version=__version__, source=n.source, bounds=b, n_nodes=n.n_nodes,
                    n_segments=n.n_segments, par=list(n.par_params), excluded=list(self.excluded), out_dir=DEFAULT_OUT)

    # ------------------------------------------------------------- Karte
    def network_view(self, minlat, minlon, maxlat, maxlon, limit):
        n = self.net
        if n is None:
            return dict(classes=KEYS, cls=[], off=[0], pts=[])
        ml, mo = self.mid
        sel = np.flatnonzero((ml >= minlat) & (ml <= maxlat) & (mo >= minlon) & (mo <= maxlon))
        if len(sel) > limit:
            keep, left = [], limit
            for k in DRAW_PRIORITY:
                s = sel[n.cls[sel] == KIDX[k]]
                if len(s) <= left:
                    keep.append(s)
                    left -= len(s)
                elif left > 0:
                    rng = np.random.default_rng(1)
                    keep.append(np.sort(rng.choice(s, left, replace=False)))
                    left = 0
            sel = np.sort(np.concatenate(keep)) if keep else sel[:0]
        if len(sel) == 0:
            return dict(classes=KEYS, cls=[], off=[0], pts=[])
        a, b, c, w = n.a[sel], n.b[sel], n.cls[sel], n.wid[sel]
        cont = (sel[1:] == sel[:-1] + 1) & (b[:-1] == a[1:]) & (c[:-1] == c[1:]) & (w[:-1] == w[1:])
        starts = np.r_[0, np.flatnonzero(~cont) + 1]
        ends = np.r_[starts[1:], len(sel)]
        pts, off, cls = [], [0], []
        for s0, e0 in zip(starts, ends):
            nodes = np.r_[a[s0:e0], b[e0 - 1]]
            pts.append(np.c_[n.lat[nodes], n.lon[nodes]].ravel())
            off.append(off[-1] + 2 * len(nodes))
            cls.append(int(c[s0]))
        flat = np.round(np.concatenate(pts), 5)
        return dict(classes=KEYS, cls=cls, off=off, pts=flat.tolist(), truncated=bool(len(sel) >= limit))

    # ------------------------------------------------------------- Route
    def _factors(self, body):
        if body.get("mode") == "paths":
            return factors_for_k(float(body.get("k", 5)))
        f = dict(FACTORS_V1)
        for k, v in (body.get("factors") or {}).items():
            if k in KIDX:
                f[k] = None if v is None else float(v)
        return f

    def do_route(self, job, body):
        n = self.net
        if n is None:
            raise RuntimeError("Lade zuerst eine Kartendatei.")
        start, end = tuple(body["start"]), tuple(body["end"])
        cb = self._prog(job)
        allowed, corridor_pts = None, None
        cor = body.get("corridor")
        if cor and cor.get("points"):
            corridor_pts = np.asarray(cor["points"], float)
            allowed = router.corridor_mask(n, corridor_pts, float(cor.get("width", 1000)))
        cb(0.1, "Suche kürzeste Vergleichsroute")
        ref = router.shortest(n, start, end)
        cb(0.4, "Berechne Route")
        r = router.solve(n, start, end, self._factors(body), allowed)
        cb(0.9, "Prüfe Zugangshinweise")
        summ = router.summarize(n, r, ref)
        summ["kuerzeste"] = router.summarize(n, ref)
        summ["mode"] = body.get("mode")
        summ["k"] = body.get("k") if body.get("mode") == "paths" else None
        summ["factors"] = {k: v for k, v in self._factors(body).items()}
        summ["start"], summ["end"] = list(start), list(end)
        acc = router.access_log(n, r)
        self.last = dict(coords=r.coords(n), ref_coords=ref.coords(n), summary=summ, access=acc)
        return dict(
            runs=router.route_runs(n, r), ref=[[float(a), float(b)] for a, b in ref.coords(n)],
            summary=summ, access=acc,
            start=[float(n.lat[r.nodes[0]]), float(n.lon[r.nodes[0]])], end=[float(n.lat[r.nodes[-1]]), float(n.lon[r.nodes[-1]])],
        )

    def do_sweep(self, job, body):
        n = self.net
        if n is None:
            raise RuntimeError("Lade zuerst eine Kartendatei.")
        cor = body.get("corridor")
        allowed = None
        if cor and cor.get("points"):
            allowed = router.corridor_mask(n, np.asarray(cor["points"], float), float(cor.get("width", 1000)))
        rows = router.sweep(n, tuple(body["start"]), tuple(body["end"]), [float(k) for k in body["ks"]], allowed,
                            progress=self._prog(job))
        return dict(rows=rows)

    def do_export(self, body):
        if not self.last:
            raise RuntimeError("Es gibt noch keine berechnete Route zum Speichern.")
        out = body.get("out_dir") or DEFAULT_OUT
        files = write_outputs(out, body.get("tag") or time.strftime("route_%Y%m%d_%H%M%S"), self.last, body.get("png"))
        return dict(files=files, dir=out)

    # ------------------------------------------------------------- Dialoge
    def pick(self, kind):  # Dateidialog
        if self.picker:
            return self.picker(kind)
        return tk_pick(kind)


def tk_pick(kind):
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "pbf":
            return filedialog.askopenfilename(title="OSM-Kartendatei wählen", filetypes=[("OSM PBF", "*.pbf"), ("Alle Dateien", "*.*")]) or None
        if kind == "gpx":
            return filedialog.askopenfilename(title="GPX-Track wählen", filetypes=[("GPX", "*.gpx"), ("Alle Dateien", "*.*")]) or None
        return filedialog.askdirectory(title="Zielordner wählen") or None
    finally:
        root.destroy()


def defaults_payload():
    return dict(
        classes=[dict(key=k, label=CLASSES[k][0], pfad=CLASSES[k][1], strasse=CLASSES[k][2], color=CLASSES[k][3],
                      v1=FACTORS_V1.get(k), kmult=K_MULT.get(k)) for k in KEYS],
        excluded=list(DEFAULT_EXCLUDED), version=__version__,
        exclude_options=[dict(key="motorway", label="Autobahn", types=["motorway", "motorway_link"]),
                         dict(key="trunk", label="Schnellstrasse", types=["trunk", "trunk_link"])],
    )


class _Enc(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        return super().default(o)


def make_handler(app: App):
    wd = web_dir()
    mime = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".woff2": "font/woff2"}

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # still
            pass

        def _host_ok(self):
            return self.headers.get("Host", "") in (f"127.0.0.1:{app.port}", f"localhost:{app.port}")

        def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, cls=_Enc).encode("utf-8"))

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            u = urlparse(self.path)
            if u.path.startswith("/api/"):
                return self._api("GET", u)
            rel = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
            p = (wd / rel).resolve()
            if wd.resolve() not in p.parents or not p.is_file():
                return self._send(404, b"not found", "text/plain")
            data = p.read_bytes()
            if p.name == "index.html":
                data = data.replace(b"__TOKEN__", app.token.encode())
            self._send(200, data, mime.get(p.suffix, "application/octet-stream"))

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            self._api("POST", urlparse(self.path))

        def _api(self, method, u):
            if self.headers.get("X-Token") != app.token:
                return self._json(dict(error="Nicht autorisiert"), 401)
            try:
                body = {}
                if method == "POST":
                    ln = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(ln) or b"{}")
                q = parse_qs(u.query)
                path = u.path
                if path == "/api/defaults":
                    return self._json(defaults_payload())
                if path == "/api/state":
                    return self._json(app.state())
                if path == "/api/job":
                    j = app.job
                    out = dict(id=j.id, kind=j.kind, state=j.state, progress=j.progress, message=j.message, error=j.error)
                    if j.state == "done":
                        out["result"] = j.result
                    return self._json(out)
                if path == "/api/cancel":
                    app.job.cancel = True
                    return self._json(dict(ok=True))
                if path == "/api/load":
                    j = app.start_job("load", lambda job: app.do_load(job, body))
                    return self._json(dict(id=j.id))
                if path == "/api/rebuild":
                    j = app.start_job("load", lambda job: app.do_rebuild(job, body))
                    return self._json(dict(id=j.id))
                if path == "/api/route":
                    j = app.start_job("route", lambda job: app.do_route(job, body))
                    return self._json(dict(id=j.id))
                if path == "/api/sweep":
                    j = app.start_job("sweep", lambda job: app.do_sweep(job, body))
                    return self._json(dict(id=j.id))
                if path == "/api/network":
                    g = lambda k: float(q[k][0])
                    return self._json(app.network_view(g("minlat"), g("minlon"), g("maxlat"), g("maxlon"), int(q.get("limit", ["150000"])[0])))
                if path == "/api/header":
                    p = body.get("path", "")
                    return self._json(dict(bbox=read_header_bbox(p) if os.path.isfile(p) else None,
                                           size=os.path.getsize(p) if os.path.isfile(p) else None))
                if path == "/api/gpx":
                    txt = body.get("text")
                    pts = parse_gpx(txt if txt is not None else open(body["path"], "rb").read())
                    return self._json(dict(points=np.round(pts, 6).tolist()))
                if path == "/api/export":
                    return self._json(app.do_export(body))
                if path == "/api/pick":
                    return self._json(dict(path=app.pick(body.get("kind", "pbf"))))
                if path == "/api/browse":
                    u2 = str(body.get("url", ""))
                    if u2.startswith("https://www.openstreetmap.org/"):
                        import webbrowser
                        webbrowser.open(u2)
                    return self._json(dict(ok=True))
                if path == "/api/open":
                    d = body.get("path", "")
                    if os.path.isdir(d):
                        if sys.platform == "win32":
                            os.startfile(d)  # noqa: S606
                        elif sys.platform == "darwin":
                            subprocess.Popen(["open", d])
                        else:
                            subprocess.Popen(["xdg-open", d])
                    return self._json(dict(ok=True))
                return self._json(dict(error="Unbekannter Aufruf"), 404)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._json(dict(error=f"{type(e).__name__}: {e}"), 400)

    return H


def serve(app: App | None = None):
    app = app or App()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    app.port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return app, srv
