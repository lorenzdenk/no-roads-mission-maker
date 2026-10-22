"""Routing: gewichteter Dijkstra mit Klassenfaktoren, optionalem Korridor und K-Sweep."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

from .network import CLASSES, IS_PFAD, IS_STRASSE, KEYS, KIDX, Network, factors_for_k


class RouteError(Exception):
    pass


@dataclass
class Route:
    nodes: np.ndarray                 # Knotenindizes
    segs: np.ndarray                  # Segmentindizes (je Schritt)
    length: float
    cost: float
    by_class: dict
    pfad_share: float
    strasse_share: float
    snap_start: float = 0.0
    snap_end: float = 0.0

    def coords(self, net: Network):
        return np.c_[net.lat[self.nodes], net.lon[self.nodes]]


def cost_array(net: Network, factors: dict) -> np.ndarray:
    """Kosten je Segment. Klassen ohne Faktor (None/<=0) sind gesperrt -> inf."""
    f = np.ones(len(KEYS))
    for k, i in KIDX.items():
        v = factors.get(k, 1.0)
        f[i] = np.inf if (v is None or v <= 0) else float(v)
    return net.length * f[net.cls]


def corridor_mask(net: Network, polyline, width_m: float) -> np.ndarray:
    """Knoten innerhalb von width_m um eine Linie (lat, lon)-Punktfolge."""
    p = np.asarray(polyline, float)
    x, y = p[:, 1] * net.kx, p[:, 0] * net.ky
    seg = np.hypot(np.diff(x), np.diff(y))
    cum = np.r_[0, np.cumsum(seg)]
    n = max(2, int(cum[-1] / 20) + 1)
    t = np.linspace(0, cum[-1], n)
    tree = cKDTree(np.c_[np.interp(t, cum, x), np.interp(t, cum, y)])
    d, _ = tree.query(np.c_[net.x, net.y])
    return d <= width_m


def _best_segments(net: Network, path: np.ndarray, w: np.ndarray) -> np.ndarray:
    pa, pb = path[:-1].astype(np.int64), path[1:].astype(np.int64)
    n = net.n_nodes
    want = np.minimum(pa, pb) * n + np.maximum(pa, pb)
    key = np.minimum(net.a, net.b).astype(np.int64) * n + np.maximum(net.a, net.b)
    hit = np.flatnonzero(np.isin(key, want))
    hk, hw = key[hit], w[hit]
    o = np.lexsort((hw, hk))
    hk, hit = hk[o], hit[o]
    first = np.r_[True, hk[1:] != hk[:-1]]
    hk, hit = hk[first], hit[first]
    return hit[np.searchsorted(hk, want)]


def _stats(net: Network, segs: np.ndarray):
    ln = net.length[segs]
    cl = net.cls[segs]
    total = float(ln.sum())
    by = {}
    for i in np.unique(cl):
        by[KEYS[i]] = float(ln[cl == i].sum())
    if total <= 0:
        return total, by, 0.0, 0.0
    return total, by, float(ln[IS_PFAD[cl]].sum() / total), float(ln[IS_STRASSE[cl]].sum() / total)


def solve(net: Network, start, end, factors: dict, allowed: Optional[np.ndarray] = None, crop: bool = True) -> Route:
    """start/end: (lat, lon). allowed: bool je Knoten (Korridor)."""
    s, ds = net.snap(*start)
    t, dt = net.snap(*end)
    if s == t:
        raise RouteError("Start und Ziel liegen am selben Wegpunkt. Wähle zwei verschiedene Punkte.")
    w = cost_array(net, factors)
    ok = np.isfinite(w)

    node_ok = np.ones(net.n_nodes, bool)
    if allowed is not None:
        node_ok = allowed.copy()
        node_ok[[s, t]] = True
    if crop:  # Ausschnitt um Start/Ziel beschleunigt die Suche
        dx, dy = abs(net.x[s] - net.x[t]), abs(net.y[s] - net.y[t])
        margin = max(6000.0, 0.7 * float(np.hypot(dx, dy)))
        tries = [margin, None]
    else:
        tries = [None]

    for margin in tries:
        m = node_ok
        if margin is not None:
            x0, x1 = sorted((net.x[s], net.x[t]))
            y0, y1 = sorted((net.y[s], net.y[t]))
            m = node_ok & (net.x >= x0 - margin) & (net.x <= x1 + margin) & (net.y >= y0 - margin) & (net.y <= y1 + margin)
        e = ok & m[net.a] & m[net.b]
        idx = np.flatnonzero(m)
        remap = np.full(net.n_nodes, -1, np.int64)
        remap[idx] = np.arange(len(idx))
        ea, eb, ew = remap[net.a[e]], remap[net.b[e]], w[e]
        # kleinstes Gewicht je Knotenpaar
        o = np.argsort(ew, kind="stable")
        r = np.concatenate([ea[o], eb[o]]); c = np.concatenate([eb[o], ea[o]]); ww = np.concatenate([ew[o], ew[o]])
        _, first = np.unique(r.astype(np.int64) * len(idx) + c, return_index=True)
        g = csr_matrix((np.maximum(ww[first], 1e-6), (r[first], c[first])), shape=(len(idx), len(idx)))
        dist, pred = dijkstra(g, directed=False, indices=int(remap[s]), return_predecessors=True)
        if np.isfinite(dist[remap[t]]):
            p = [int(remap[t])]
            while p[-1] != remap[s]:
                p.append(int(pred[p[-1]]))
            path = idx[np.array(p[::-1])]
            cost = float(dist[remap[t]])
            break
    else:
        if allowed is not None:
            raise RouteError("Im Korridor gibt es keine Verbindung. Verbreitere den Korridor oder erlaube mehr Wegtypen.")
        raise RouteError("Zwischen Start und Ziel gibt es keine Verbindung mit den erlaubten Wegtypen. Prüfe gesperrte Wegtypen und die Lage der Punkte.")

    segs = _best_segments(net, path, w)
    total, by, ps, ss = _stats(net, segs)
    return Route(nodes=path, segs=segs, length=total, cost=cost, by_class=by, pfad_share=ps, strasse_share=ss,
                 snap_start=ds, snap_end=dt)


def shortest(net: Network, start, end) -> Route:
    return solve(net, start, end, {k: 1.0 for k in KEYS})


# ---------------------------------------------------------------- Zugangsprotokoll
CRIT_ACCESS = {"private", "no", "customers", "delivery", "permit", "agricultural", "forestry"}


def _issues(tags: dict, barriers: list) -> list:
    r = []
    for key in ("access", "foot"):
        v = tags.get(key)
        if v in CRIT_ACCESS or v in ("use_sidepath", "permissive"):
            r.append(f"{key}={v}")
    for key in ("seasonal", "access:conditional", "foot:conditional", "opening_hours", "ford", "motorroad", "indoor"):
        if key in tags:
            r.append(f"{key}={tags[key]}")
    if tags.get("tunnel") not in (None, "no"):
        r.append(f"tunnel={tags['tunnel']}")
    for b in barriers:
        r.append("barrier=" + b["barrier"] + (f" access={b['access']}" if "access" in b else "") + f" (Knoten {b['node']})")
    return r


def access_log(net: Network, route: Route) -> list:
    """Abschnitte der Route (je Weg und Klasse) mit Hinweisen auf Zugangsbeschraenkungen. Wird nur protokolliert."""
    out, cur = [], None
    nodes = route.nodes
    for k, i in enumerate(route.segs):
        wid = int(net.wid[i])
        ck = KEYS[net.cls[i]]
        if cur is None or cur["way"] != wid or cur["cls"] != ck:
            tags = net.way_tags.get(wid, {})
            cur = dict(way=wid, name=tags.get("name", ""), highway=tags.get("highway", ""), cls=ck,
                       length=0.0, tags=tags, barriers=[])
            out.append(cur)
        cur["length"] += float(net.length[i])
        for nd in ((nodes[k + 1],) + ((nodes[0],) if k == 0 else ())):
            nt = net.ntags.get(int(net.node_id[nd]))
            if nt and nt.get("barrier") and nt["barrier"] != "kerb":
                cur["barriers"].append(dict(node=int(net.node_id[nd]), **nt))
    rows = []
    for e in out:
        rows.append(dict(way=e["way"], name=e["name"], highway=e["highway"], cls=e["cls"],
                         klasse=CLASSES[e["cls"]][0], laenge_m=round(e["length"]),
                         hinweise=_issues(e["tags"], e["barriers"]),
                         link=f"https://www.openstreetmap.org/way/{e['way']}"))
    return rows


# ---------------------------------------------------------------- Hilfen fuer GUI/Export
def route_runs(net: Network, route: Route) -> list:
    """Route als Polylinien gleicher Klasse: [{cls, pts:[[lat,lon],...]}, ...]"""
    runs, cur = [], None
    for k, i in enumerate(route.segs):
        c = KEYS[net.cls[i]]
        pa, pb = route.nodes[k], route.nodes[k + 1]
        if cur is None or cur["cls"] != c:
            cur = {"cls": c, "pts": [[float(net.lat[pa]), float(net.lon[pa])]]}
            runs.append(cur)
        cur["pts"].append([float(net.lat[pb]), float(net.lon[pb])])
    return runs


def summarize(net: Network, route: Route, ref: Optional[Route] = None) -> dict:
    d = dict(
        length_m=round(route.length, 1),
        pfad_share=round(route.pfad_share, 4),
        strasse_share=round(route.strasse_share, 4),
        by_class={k: round(v, 1) for k, v in sorted(route.by_class.items(), key=lambda kv: -kv[1])},
        snap_start_m=round(route.snap_start, 1),
        snap_end_m=round(route.snap_end, 1),
    )
    if ref is not None:
        d["umweg_vs_kuerzeste"] = round(route.length / ref.length - 1, 4) if ref.length else 0.0
    return d


def sweep(net: Network, start, end, ks, allowed=None, progress=None):
    """Pfad-Maximierung fuer mehrere K. Gibt Zeilen mit Laenge/Pfadanteil/Strassenanteil zurueck."""
    ref = shortest(net, start, end)
    rows = [dict(k=None, length_m=ref.length, pfad_share=ref.pfad_share, strasse_share=ref.strasse_share, umweg=0.0,
                 label="kürzeste")]
    for n, k in enumerate(ks):
        if progress:
            progress(n / max(len(ks), 1), f"K = {k:g}")
        r = solve(net, start, end, factors_for_k(k), allowed)
        rows.append(dict(k=float(k), length_m=r.length, pfad_share=r.pfad_share, strasse_share=r.strasse_share,
                         umweg=r.length / ref.length - 1 if ref.length else 0.0, label=f"K={k:g}"))
    return rows
