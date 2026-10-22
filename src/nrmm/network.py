"""Wegenetz aus OSM-Daten: Klassifikation, Parallel-Erkennung, Segmenttabelle."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from .pbf import OsmData

# ---------------------------------------------------------------- Klassen
# key: (Bezeichnung, ist_pfad, strassenartig, Farbe fuer die Karte)
CLASSES = {
    "pfad":       ("Pfad (path, footway, Treppen)",             True,  False, "#ff5a4d"),
    "feldweg":    ("Feld- und Forstweg (track)",                 False, False, "#d49a2a"),
    "fussg":      ("Fußgängerzone, Wohnstraße",                  False, False, "#2db89a"),
    "radweg":     ("Radweg, eigenständig",                       False, False, "#8d6bdc"),
    "quer":       ("Querung",                                    False, False, "#f2c230"),
    "pfad_par":   ("Pfad parallel zur Straße",                   False, True,  "#e0489a"),
    "radweg_par": ("Radweg parallel zur Straße",                 False, True,  "#b0578f"),
    "sidewalk":   ("Gehweg an der Straße",                       False, True,  "#a8763a"),
    "service":    ("Zufahrt, Serviceweg",                        False, True,  "#7a8794"),
    "nebenstr":   ("Nebenstraße (residential, tertiary)",        False, True,  "#4b5563"),
    "secondary":  ("Hauptstraße (secondary)",                    False, True,  "#2f3640"),
    "primary":    ("Fernstraße (primary)",                       False, True,  "#16191f"),
}
KEYS = list(CLASSES)
KIDX = {k: i for i, k in enumerate(KEYS)}
IS_PFAD = np.array([CLASSES[k][1] for k in KEYS])
IS_STRASSE = np.array([CLASSES[k][2] for k in KEYS])
ROAD_REF = ("nebenstr", "secondary", "primary")   # Referenz fuer die Parallel-Erkennung

# Voreinstellungen
FACTORS_V1 = {  # "Straßenarm" (route.py), vom Nutzer angepasst
    "pfad": 1.0, "feldweg": 1.0, "fussg": 1.0, "radweg": 1.5, "quer": 3.0,
    "pfad_par": 16.0, "radweg_par": 16.0, "sidewalk": 20.0, "service": 12.0,
    "nebenstr": 20.0, "secondary": 40.0, "primary": 60.0,
}
# "Pfad-Maximierung" (route2.py): Pfade 1, alles andere K * Multiplikator
K_MULT = {
    "feldweg": 1.0, "fussg": 1.0, "radweg": 1.25, "pfad_par": 2.0, "radweg_par": 2.0,
    "sidewalk": 2.0, "service": 2.0, "nebenstr": 3.0, "secondary": 5.0, "primary": 8.0,
}
DEFAULT_EXCLUDED = ("motorway", "motorway_link", "trunk", "trunk_link")


def factors_for_k(k: float) -> dict:
    f = {"pfad": 1.0, "quer": 3.0}
    for key, m in K_MULT.items():
        f[key] = m * k
    return f


def classify(t: dict, excluded=DEFAULT_EXCLUDED):
    """-> (klassen-key, kann_parallel_sein) oder None, wenn ausgeschlossen."""
    h = t.get("highway")
    if h is None or h in excluded or t.get("area") == "yes":
        return None
    if h in ("primary", "primary_link"):
        return "primary", False
    if h in ("secondary", "secondary_link"):
        return "secondary", False
    if h in ("tertiary", "tertiary_link", "residential", "unclassified", "road"):
        return "nebenstr", False
    if h == "service":
        return "service", False
    if h in ("motorway", "motorway_link", "trunk", "trunk_link"):  # nur wenn nicht ausgeschlossen
        return "primary", False
    if h in ("living_street", "pedestrian", "corridor"):
        return "fussg", False
    if h in ("track", "bridleway"):
        return "feldweg", False
    if h == "steps":
        return "pfad", False
    if h in ("footway", "path", "cycleway"):
        fw = t.get("footway")
        if fw == "sidewalk" or t.get("path") == "sidewalk":
            return "sidewalk", False
        if fw in ("crossing", "traffic_island") or t.get("cycleway") == "crossing" or "crossing" in t:
            return "quer", False
        return ("radweg" if h == "cycleway" else "pfad"), True
    return None


@dataclass
class Network:
    # Knoten (nur genutzte)
    node_id: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    x: np.ndarray                  # lokale Meter
    y: np.ndarray
    # Segmente
    a: np.ndarray                  # Knotenindex (in diesem Netz)
    b: np.ndarray
    wid: np.ndarray
    base_cls: np.ndarray           # Klasse vor Parallel-Erkennung
    maybe_par: np.ndarray
    length: np.ndarray
    cls: np.ndarray                # effektive Klasse
    way_tags: dict
    ntags: dict
    kx: float
    ky: float
    main_comp: np.ndarray          # bool je Knoten: groesste Komponente
    tree: cKDTree                  # KD-Baum der Knoten der Hauptkomponente
    tree_idx: np.ndarray
    par_params: tuple = (10.0, 0.5)
    source: str = ""

    @property
    def n_nodes(self) -> int:
        return len(self.node_id)

    @property
    def n_segments(self) -> int:
        return len(self.a)

    def bounds(self):
        return float(self.lat.min()), float(self.lon.min()), float(self.lat.max()), float(self.lon.max())

    def project(self, lat, lon):
        return np.asarray(lon) * self.kx, np.asarray(lat) * self.ky

    # ----------------------------------------------------------- Parallel-Erkennung
    def detect_parallel(self, dist_m: float = 10.0, share: float = 0.5, step_m: float = 5.0) -> None:
        """Pfade/Radwege, die ueberwiegend dichter als dist_m an einer Straße liegen, gelten als strassenbegleitend."""
        ua, ub = self.a, self.b
        xu, yu, xv, yv = self.x[ua], self.y[ua], self.x[ub], self.y[ub]
        road = np.isin(self.base_cls, [KIDX[k] for k in ROAD_REF])
        cls = self.base_cls.copy()
        rs = np.flatnonzero(road)
        cand = np.flatnonzero(self.maybe_par)
        if len(rs) and len(cand) and dist_m > 0:
            ns = np.maximum(1, np.ceil(self.length[rs] / step_m).astype(int))
            rep = np.repeat(rs, ns)
            fr = (np.arange(len(rep)) - np.repeat(np.cumsum(ns) - ns, ns) + .5) / np.repeat(ns, ns)
            tr = cKDTree(np.c_[xu[rep] + fr * (xv[rep] - xu[rep]), yu[rep] + fr * (yv[rep] - yu[rep])])
            nc = np.maximum(1, np.ceil(self.length[cand] / step_m).astype(int))
            rep2 = np.repeat(cand, nc)
            fr2 = (np.arange(len(rep2)) - np.repeat(np.cumsum(nc) - nc, nc) + .5) / np.repeat(nc, nc)
            d, _ = tr.query(np.c_[xu[rep2] + fr2 * (xv[rep2] - xu[rep2]), yu[rep2] + fr2 * (yv[rep2] - yu[rep2])])
            sh = np.bincount(np.searchsorted(cand, rep2), weights=(d < dist_m).astype(float), minlength=len(cand)) / nc
            par = np.zeros(len(ua), bool)
            par[cand[sh >= share]] = True
            cls[par & (self.base_cls == KIDX["pfad"])] = KIDX["pfad_par"]
            cls[par & (self.base_cls == KIDX["radweg"])] = KIDX["radweg_par"]
        self.cls = cls
        self.par_params = (dist_m, share)

    # ----------------------------------------------------------- Snap
    def snap(self, lat: float, lon: float):
        """Naechster Knoten der Hauptkomponente -> (Knotenindex, Abstand in m)."""
        d, i = self.tree.query([lon * self.kx, lat * self.ky])
        return int(self.tree_idx[i]), float(d)


def build_network(data: OsmData, excluded=DEFAULT_EXCLUDED, par_dist=10.0, par_share=0.5, progress=None) -> Network:
    def prog(p, m):
        if progress:
            progress(p, m)

    prog(0.0, "Klassifiziere Wege")
    su, sv, sw, sc, sp = [], [], [], [], []
    way_tags = {}
    for wid, refs, t in data.ways:
        c = classify(t, excluded)
        if c is None or len(refs) < 2:
            continue
        key, maybe = c
        way_tags[wid] = t
        n = len(refs) - 1
        su.append(refs[:-1]); sv.append(refs[1:])
        sw.append(np.full(n, wid, np.int64))
        sc.append(np.full(n, KIDX[key], np.int8))
        sp.append(np.full(n, maybe, bool))
    if not su:
        raise ValueError("In den Daten wurden keine begehbaren Wege gefunden.")
    su, sv, sw, sc, sp = (np.concatenate(x) for x in (su, sv, sw, sc, sp))

    prog(0.25, "Ordne Koordinaten zu")
    ids = data.ids
    iu = np.searchsorted(ids, su)
    iv = np.searchsorted(ids, sv)
    last = len(ids) - 1
    ok = (iu <= last) & (iv <= last)
    ok &= (ids[np.minimum(iu, last)] == su) & (ids[np.minimum(iv, last)] == sv)
    su, sv, sw, sc, sp, iu, iv = (x[ok] for x in (su, sv, sw, sc, sp, iu, iv))

    used = np.unique(np.concatenate([iu, iv]))
    remap = np.full(len(ids), -1, np.int64)
    remap[used] = np.arange(len(used))
    a, b = remap[iu], remap[iv]
    lat, lon = data.lat[used], data.lon[used]
    lat0 = np.deg2rad(float(lat.mean()))
    kx, ky = 111320.0 * float(np.cos(lat0)), 110574.0
    x, y = lon * kx, lat * ky
    length = np.hypot(x[b] - x[a], y[b] - y[a])

    prog(0.45, "Suche zusammenhängendes Netz")
    n = len(used)
    g = csr_matrix((np.ones(len(a), np.int8), (a, b)), shape=(n, n))
    _, labels = connected_components(g, directed=False)
    big = np.argmax(np.bincount(labels))
    main = labels == big
    tree_idx = np.flatnonzero(main)
    tree = cKDTree(np.c_[x[tree_idx], y[tree_idx]])

    net = Network(
        node_id=ids[used], lat=lat, lon=lon, x=x, y=y, a=a, b=b, wid=sw, base_cls=sc, maybe_par=sp,
        length=length, cls=sc.copy(), way_tags=way_tags, ntags=data.ntags, kx=kx, ky=ky,
        main_comp=main, tree=tree, tree_idx=tree_idx, source=data.source,
    )
    prog(0.6, "Erkenne straßenbegleitende Wege")
    net.detect_parallel(par_dist, par_share)
    prog(1.0, "Netz bereit")
    return net
