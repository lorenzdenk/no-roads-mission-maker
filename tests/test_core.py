import numpy as np
import pytest

from nrmm.export import gpx_text, parse_gpx
from nrmm.network import FACTORS_V1, build_network, classify, factors_for_k
from nrmm.pbf import OsmData
from nrmm.router import RouteError, corridor_mask, shortest, solve, summarize


def make_data(road_tag="primary", path_tag="path"):
    """Direkte Straße von A nach B (Süd) und ein Pfad-Umweg (Nord, 400 m entfernt)."""
    lon = np.linspace(9.000, 9.006, 7)
    nodes = {}
    nid = 1
    road = []
    for lo in lon:
        nodes[nid] = (47.000, lo); road.append(nid); nid += 1
    path = [road[0]]
    for lo in lon:
        nodes[nid] = (47.004, lo); path.append(nid); nid += 1
    path.append(road[-1])
    ids = np.array(sorted(nodes), dtype=np.int64)
    lat = np.array([nodes[i][0] for i in ids])
    lo = np.array([nodes[i][1] for i in ids])
    ways = [
        (1, np.array(road, dtype=np.int64), {"highway": road_tag}),
        (2, np.array(path, dtype=np.int64), {"highway": path_tag}),
    ]
    return OsmData(ids=ids, lat=lat, lon=lo, ways=ways, ntags={})


A = (47.000, 9.000)
B = (47.000, 9.006)


def test_classify_basics():
    assert classify({"highway": "path"}) == ("pfad", True)
    assert classify({"highway": "primary"}) == ("primary", False)
    assert classify({"highway": "motorway"}) is None
    assert classify({"highway": "track"}) == ("feldweg", False)
    assert classify({"highway": "footway", "footway": "sidewalk"})[0] == "sidewalk"


def test_prefers_path_over_road():
    net = build_network(make_data())
    r = solve(net, A, B, FACTORS_V1, crop=False)
    sh = shortest(net, A, B)
    assert r.length > sh.length * 1.5          # Umweg über den Pfad
    assert r.pfad_share > 0.9
    assert r.strasse_share < 0.1
    assert summarize(net, r, sh)["umweg_vs_kuerzeste"] > 0.5


def test_blocked_class_forces_other_way():
    net = build_network(make_data())
    f = dict(FACTORS_V1); f["pfad"] = None     # Pfade gesperrt
    r = solve(net, A, B, f, crop=False)
    assert r.strasse_share > 0.9


def test_cheap_road_wins_when_factors_equal():
    net = build_network(make_data())
    f = {k: 1.0 for k in FACTORS_V1}
    r = solve(net, A, B, f, crop=False)
    assert r.length == pytest.approx(shortest(net, A, B).length, rel=0.01)


def test_k_factors_monotonic():
    f1, f2 = factors_for_k(2), factors_for_k(20)
    assert f1["pfad"] == f2["pfad"] == 1.0
    assert f2["primary"] > f1["primary"]


def test_same_point_raises():
    net = build_network(make_data())
    with pytest.raises(RouteError):
        solve(net, A, A, FACTORS_V1)


def test_corridor_excludes_far_nodes():
    net = build_network(make_data())
    southern = np.array([[47.000, 9.000], [47.000, 9.006]])
    mask = corridor_mask(net, southern, 50.0)
    assert mask.sum() < len(mask)
    r = solve(net, A, B, FACTORS_V1, allowed=mask, crop=False)
    assert r.strasse_share > 0.9               # Pfad liegt außerhalb des Korridors


def test_gpx_roundtrip():
    pts = [(47.1, 9.2), (47.2, 9.3), (47.25, 9.35)]
    back = parse_gpx(gpx_text(pts, "t"))
    assert back.shape == (3, 2)
    assert np.allclose(back, pts, atol=1e-6)


def test_parallel_detection():
    """Ein Pfad direkt neben einer Straße zählt als straßenbegleitend."""
    data = make_data(path_tag="path")
    # Pfad auf 3 m Abstand zur Straße verlegen
    data.lat[data.ids > 7] = 47.00003
    net = build_network(data)
    from nrmm.network import KIDX
    assert (net.cls == KIDX["pfad_par"]).any()
