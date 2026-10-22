"""Schneller OSM-PBF-Leser (nur numpy + stdlib).

Liest Knoten (DenseNodes), Wege mit highway-Tag und wenige Knoten-Tags (Schranken, Zugang).
Optional mit Ausschnitt (bbox), Fortschrittsmeldung, Abbruch und Cache.
"""
from __future__ import annotations

import hashlib
import os
import pickle
import struct
import zlib
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

NODE_TAG_KEYS = {"barrier", "access", "foot", "bicycle", "motor_vehicle", "gate", "locked", "crossing"}
CACHE_VERSION = 3


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------- Protobuf-Grundlagen
def _varints_np(buf: bytes) -> np.ndarray:
    """Alle Varints eines gepackten Feldes, vektorisiert -> uint64."""
    a = np.frombuffer(buf, dtype=np.uint8)
    if a.size == 0:
        return np.zeros(0, dtype=np.uint64)
    ends = np.flatnonzero(a < 0x80)
    starts = np.empty_like(ends)
    starts[0] = 0
    starts[1:] = ends[:-1] + 1
    lens = ends - starts + 1
    out = np.zeros(len(ends), dtype=np.uint64)
    for j in range(int(lens.max())):
        m = lens > j
        out[m] |= (a[starts[m] + j] & 0x7F).astype(np.uint64) << np.uint64(7 * j)
    return out


def _unzig_np(v: np.ndarray) -> np.ndarray:
    v = v.astype(np.uint64, copy=False)
    return (v >> np.uint64(1)).astype(np.int64) ^ (-(v & np.uint64(1)).astype(np.int64))


def _varints_py(buf: bytes) -> list:
    out, i, n = [], 0, len(buf)
    while i < n:
        x = s = 0
        while True:
            b = buf[i]
            i += 1
            x |= (b & 0x7F) << s
            if b < 0x80:
                break
            s += 7
        out.append(x)
    return out


def _varints(buf: bytes):
    return _varints_py(buf) if len(buf) < 96 else _varints_np(buf)


def _unzig(v: int) -> int:
    return (v >> 1) ^ -(v & 1)


def _fields(buf: bytes):
    """Iteriert (feldnr, wiretype, wert) einer Protobuf-Nachricht."""
    i, n = 0, len(buf)
    while i < n:
        key = s = 0
        while True:
            b = buf[i]
            i += 1
            key |= (b & 0x7F) << s
            if b < 0x80:
                break
            s += 7
        fn, wt = key >> 3, key & 7
        if wt == 0:
            x = s = 0
            while True:
                b = buf[i]
                i += 1
                x |= (b & 0x7F) << s
                if b < 0x80:
                    break
                s += 7
            yield fn, wt, x
        elif wt == 2:
            ln = s = 0
            while True:
                b = buf[i]
                i += 1
                ln |= (b & 0x7F) << s
                if b < 0x80:
                    break
                s += 7
            yield fn, wt, buf[i:i + ln]
            i += ln
        elif wt == 1:
            yield fn, wt, buf[i:i + 8]
            i += 8
        elif wt == 5:
            yield fn, wt, buf[i:i + 4]
            i += 4
        else:
            raise ValueError(f"Unbekannter Wiretype {wt}")


def _blocks(f):
    while True:
        h = f.read(4)
        if not h:
            return
        hl = struct.unpack(">I", h)[0]
        hdr = f.read(hl)
        typ, dsz = None, 0
        for fn, _, v in _fields(hdr):
            if fn == 1:
                typ = v.decode()
            elif fn == 3:
                dsz = v
        blob = f.read(dsz)
        raw = None
        for fn, _, v in _fields(blob):
            if fn == 1:
                raw = bytes(v)
            elif fn == 3:
                raw = zlib.decompress(v)
        yield typ, raw


# ---------------------------------------------------------------- Ergebnis
@dataclass
class OsmData:
    ids: np.ndarray                 # sortierte Knoten-IDs
    lat: np.ndarray
    lon: np.ndarray
    ways: list                      # (way_id, refs ndarray int64, tags dict)
    ntags: dict = field(default_factory=dict)   # node_id -> Tags (Schranken usw.)
    bbox: Optional[dict] = None     # left, right, top, bottom (Header)
    source: str = ""

    @property
    def n_nodes(self) -> int:
        return int(len(self.ids))


def read_header_bbox(path: str) -> Optional[dict]:
    """Liest nur den Header: Ausdehnung der Datei (falls vorhanden)."""
    with open(path, "rb") as f:
        for typ, raw in _blocks(f):
            if typ == "OSMHeader":
                for fn, _, v in _fields(raw):
                    if fn == 1:
                        bb = {f2: _unzig(v2) * 1e-9 for f2, _, v2 in _fields(v)}
                        return dict(left=bb.get(1), right=bb.get(2), top=bb.get(3), bottom=bb.get(4))
                return None
            break
    return None


def _cache_path(path: str, bbox, cache_dir: str) -> str:
    st = os.stat(path)
    key = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}|{bbox}|{CACHE_VERSION}"
    h = hashlib.sha1(key.encode()).hexdigest()[:16]
    return os.path.join(cache_dir, f"{os.path.splitext(os.path.basename(path))[0]}-{h}.nrmm-cache")


def read_pbf(
    path: str,
    bbox: Optional[tuple] = None,           # (min_lat, min_lon, max_lat, max_lon)
    progress: Optional[Callable[[float, str], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
    cache_dir: Optional[str] = None,
) -> OsmData:
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cp = _cache_path(path, bbox, cache_dir)
        if os.path.exists(cp):
            try:
                with open(cp, "rb") as fh:
                    d = pickle.load(fh)
                if progress:
                    progress(1.0, "Aus Zwischenspeicher geladen")
                return d
            except Exception:
                pass

    total = os.path.getsize(path)
    ids_l, lat_l, lon_l = [], [], []
    ways, ntags = [], {}
    bb_header = None
    ids_sorted = None
    sorted_ready = False

    with open(path, "rb") as f:
        for typ, raw in _blocks(f):
            if cancel and cancel():
                raise Cancelled()
            if progress:
                progress(min(0.99, f.tell() / total), "Lese Kartendaten")
            if typ == "OSMHeader":
                for fn, _, v in _fields(raw):
                    if fn == 1:
                        bb = {f2: _unzig(v2) * 1e-9 for f2, _, v2 in _fields(v)}
                        bb_header = dict(left=bb.get(1), right=bb.get(2), top=bb.get(3), bottom=bb.get(4))
                continue
            if typ != "OSMData":
                continue
            strings, groups = [], []
            gran, lat_off, lon_off = 100, 0, 0
            for fn, _, v in _fields(raw):
                if fn == 1:
                    strings = [bytes(s) for f2, _, s in _fields(v) if f2 == 1]
                elif fn == 2:
                    groups.append(v)
                elif fn == 17:
                    gran = v
                elif fn == 19:
                    lat_off = v
                elif fn == 20:
                    lon_off = v
            sd = None  # Strings erst bei Bedarf dekodieren

            for g in groups:
                for fn, _, v in _fields(g):
                    if fn == 2:  # DenseNodes
                        d_id = d_lat = d_lon = d_kv = None
                        for f2, _, v2 in _fields(v):
                            if f2 == 1:
                                d_id = v2
                            elif f2 == 8:
                                d_lat = v2
                            elif f2 == 9:
                                d_lon = v2
                            elif f2 == 10:
                                d_kv = v2
                        a = np.cumsum(_unzig_np(_varints_np(d_id)))
                        b = np.cumsum(_unzig_np(_varints_np(d_lat)))
                        c = np.cumsum(_unzig_np(_varints_np(d_lon)))
                        la = (lat_off + gran * b) * 1e-9
                        lo = (lon_off + gran * c) * 1e-9
                        if d_kv:
                            if sd is None:
                                sd = [s.decode("utf-8", "replace") for s in strings]
                            kv = _varints_np(d_kv).astype(np.int64)
                            zero = np.flatnonzero(kv == 0)
                            if len(zero) and len(kv) > len(zero):  # es gibt getaggte Knoten
                                start = 0
                                for idx, z in enumerate(zero):
                                    if z - start >= 2:
                                        cur = {}
                                        for j in range(start, z, 2):
                                            k = sd[kv[j]]
                                            if k in NODE_TAG_KEYS:
                                                cur[k] = sd[kv[j + 1]]
                                        if cur and ("barrier" in cur or "access" in cur or "foot" in cur):
                                            ntags[int(a[idx])] = cur
                                    start = z + 1
                        if bbox is not None:
                            m = (la >= bbox[0]) & (la <= bbox[2]) & (lo >= bbox[1]) & (lo <= bbox[3])
                            a, la, lo = a[m], la[m], lo[m]
                        ids_l.append(a)
                        lat_l.append(la)
                        lon_l.append(lo)
                    elif fn == 3:  # Way
                        wid, keys, vals, refs = 0, None, None, None
                        for f2, _, v2 in _fields(v):
                            if f2 == 1:
                                wid = v2
                            elif f2 == 2:
                                keys = v2
                            elif f2 == 3:
                                vals = v2
                            elif f2 == 8:
                                refs = v2
                        if keys is None or refs is None:
                            continue
                        if sd is None:
                            sd = [s.decode("utf-8", "replace") for s in strings]
                        ks, vs = _varints(keys), _varints(vals)
                        tags = {sd[k]: sd[x] for k, x in zip(ks, vs)}
                        if "highway" not in tags:
                            continue
                        if not sorted_ready:  # Knoten stehen in PBF vor den Wegen
                            if ids_l:
                                ids_sorted = np.unique(np.concatenate(ids_l))
                            else:
                                ids_sorted = np.zeros(0, dtype=np.int64)
                            sorted_ready = True
                        r = np.cumsum(np.asarray([_unzig(x) for x in _varints_py(refs)], dtype=np.int64)
                                      if len(refs) < 96 else _unzig_np(_varints_np(refs)))
                        if bbox is not None:
                            pos = np.searchsorted(ids_sorted, r)
                            pos[pos >= len(ids_sorted)] = max(len(ids_sorted) - 1, 0)
                            if len(ids_sorted) == 0 or not (ids_sorted[pos] == r).any():
                                continue
                        ways.append((wid, r, tags))

    if ids_l:
        ids = np.concatenate(ids_l)
        lat = np.concatenate(lat_l)
        lon = np.concatenate(lon_l)
        o = np.argsort(ids, kind="stable")
        ids, lat, lon = ids[o], lat[o], lon[o]
    else:
        ids = np.zeros(0, np.int64)
        lat = lon = np.zeros(0)
    data = OsmData(ids=ids, lat=lat, lon=lon, ways=ways, ntags=ntags, bbox=bb_header, source=os.path.basename(path))
    if cache_dir:
        try:
            with open(cp, "wb") as fh:
                pickle.dump(data, fh, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:
            pass
    if progress:
        progress(1.0, "Fertig")
    return data
