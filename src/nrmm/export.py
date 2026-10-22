"""Dateien schreiben: GPX, Zugangsprotokoll (CSV), Zusammenfassung (JSON)."""
from __future__ import annotations

import base64
import csv
import io
import json
import os
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import numpy as np


def gpx_text(coords, name: str) -> str:
    pts = "\n".join(f'      <trkpt lat="{la:.7f}" lon="{lo:.7f}"/>' for la, lo in coords)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="No Roads Mission Maker" xmlns="http://www.topografix.com/GPX/1/1">\n'
        f"  <trk><name>{escape(name)}</name><trkseg>\n{pts}\n  </trkseg></trk>\n</gpx>\n"
    )


def parse_gpx(data) -> np.ndarray:
    """Liest Track- oder Routenpunkte aus GPX (bytes oder str) -> Array (lat, lon)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    root = ET.fromstring(data.lstrip(b"\xef\xbb\xbf"))
    pts = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("trkpt", "rtept") and "lat" in el.attrib and "lon" in el.attrib:
            pts.append((float(el.attrib["lat"]), float(el.attrib["lon"])))
    if len(pts) < 2:
        raise ValueError("In der GPX-Datei wurden keine Track- oder Routenpunkte gefunden.")
    return np.array(pts)


def access_csv(rows) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["way_id", "name", "highway", "klasse", "laenge_m", "hinweise", "osm_link"])
    for r in rows:
        w.writerow([r["way"], r["name"], r["highway"], r["klasse"], r["laenge_m"], "; ".join(r["hinweise"]), r["link"]])
    return buf.getvalue()


def write_outputs(outdir: str, tag: str, result: dict, png_b64: str | None = None) -> list:
    """result: dict mit coords, ref_coords, summary, access (siehe server/cli). Gibt die geschriebenen Pfade zurueck."""
    os.makedirs(outdir, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", tag) or "route"
    written = []

    def put(name, text=None, raw=None):
        p = os.path.join(outdir, name)
        if raw is not None:
            with open(p, "wb") as f:
                f.write(raw)
        else:
            with open(p, "w", encoding="utf-8-sig" if name.endswith(".csv") else "utf-8", newline="") as f:
                f.write(text)
        written.append(p)

    put(f"{safe}_route.gpx", gpx_text(result["coords"], f"Route {safe}"))
    if result.get("ref_coords") is not None:
        put(f"{safe}_kuerzeste.gpx", gpx_text(result["ref_coords"], f"Kuerzeste {safe}"))
    put(f"{safe}_zugangsprotokoll.csv", access_csv(result["access"]))
    put(f"{safe}_zusammenfassung.json", json.dumps(result["summary"], indent=1, ensure_ascii=False))
    if png_b64:
        raw = base64.b64decode(png_b64.split(",", 1)[-1])
        put(f"{safe}_karte.png", raw=raw)
    return written
