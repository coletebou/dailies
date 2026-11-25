import io
import json
import math

import pytest
from PIL import Image
from pydantic import ValidationError

from dailies_server.catalog import Catalog
from dailies_server.store import ID_RE, AnnotationIn, Annotations, StoreError
from dailies_server.util import atomic_write_json, valid_name


def _png(w=64, h=36) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (239, 84, 30)).save(buf, format="PNG")
    return buf.getvalue()


def test_annotation_roundtrip_persists_pretty_json(client, settings):
    r = client.post("/api/annotations", json={"version": "v02", "time": 1.52, "tag": "problem", "text": " too dark "})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["frame"] == 45 and a["time"] == 1.5 and a["shot"] == "s02"      # 1.52 s shows frame 45 (s02: 30-75)
    assert a["text"] == "too dark" and a["status"] == "open" and a["author"] == "local"
    raw = settings.annotations_file.read_text()
    assert raw.endswith("\n") and '\n  "annotations": [\n' in raw         # pretty-printed for git
    d = json.loads(raw)
    assert d["schema"] == 1 and d["annotations"][0]["id"] == a["id"]

    r = client.post("/api/annotations", json={"version": "v01", "time": 0.0, "frame": 3, "tag": "like"})
    b = r.json()
    assert b["shot"] == "s01" and b["time"] == 0.1
    ids = [x["id"] for x in json.loads(settings.annotations_file.read_text())["annotations"]]
    assert ids == [b["id"], a["id"]]                                        # sorted by version, then frame

    r = client.patch(f"/api/annotations/{a['id']}", json={"status": "done", "text": "fixed in v07", "frame": 80})
    assert r.status_code == 200
    assert r.json()["status"] == "done" and r.json()["shot"] == "s03"       # shot follows the moved frame
    # a fresh store (server restart) reads the same thing back
    again = Annotations(settings, Catalog(settings)).get(a["id"])
    assert again["text"] == "fixed in v07" and again["frame"] == 80

    assert client.delete(f"/api/annotations/{b['id']}").status_code == 200
    assert client.delete(f"/api/annotations/{b['id']}").status_code == 404
    assert [x["id"] for x in client.get("/api/annotations").json()["annotations"]] == [a["id"]]


def test_annotation_validation(client):
    assert client.post("/api/annotations", json={"version": "v99", "time": 1}).status_code == 400   # unknown version
    assert client.post("/api/annotations", json={"version": "v01", "time": 1, "tag": "meh"}).status_code == 422
    assert client.post("/api/annotations", json={"version": "v01", "time": -1}).status_code == 422
    assert client.post("/api/annotations", json={"version": "v01", "time": 1, "shot": "s77"}).status_code == 400
    r = client.post("/api/annotations", json={"version": "v01", "time": 1}, headers={"X-Dailies": "0"})
    assert r.status_code == 403                                                   # CSRF guard


def test_drawing_is_vector_json_with_inline_points(client, settings):
    drawing = {"aspect": 1.7777777, "shapes": [
        {"type": "pen", "color": "#EF541E", "size": 0.006, "points": [[0.1, 0.2, 0.5], [0.123456, 0.25, 0.6]]},
        {"type": "arrow", "color": "#e9e4db", "size": 0.004, "a": [0.5, 0.5], "b": [0.7, 0.6]},
        {"type": "ellipse", "color": "#e9e4db", "size": 0.004, "a": [0.2, 0.2], "b": [0.3, 0.4]},
        {"type": "text", "color": "#ffffff", "size": 0.04, "a": [0.4, 0.1], "text": "this"}]}
    r = client.post("/api/annotations", json={"version": "v01", "time": 2.0, "frame": 60, "tag": "idea",
                                              "drawing": drawing})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["drawing"]["shapes"][0]["color"] == "#ef541e"
    assert a["drawing"]["shapes"][0]["points"][1] == [0.1235, 0.25, 0.6]      # rounded to 4 dp
    raw = settings.annotations_file.read_text()
    assert '"points": [[0.1,0.2,0.5],[0.1235,0.25,0.6]]' in raw               # one line per stroke, not per number

    bad = {"shapes": [{"type": "pen", "points": [[5, 5]]}]}
    assert client.post("/api/annotations", json={"version": "v01", "time": 1, "drawing": bad}).status_code == 422
    bad = {"shapes": [{"type": "rect", "a": [0, 0]}]}
    assert client.post("/api/annotations", json={"version": "v01", "time": 1, "drawing": bad}).status_code == 422

    # the composited frame is stored as notes/studio/frames/<id>.png, only if it is a real PNG
    url = f"/api/annotations/{a['id']}/frame"
    assert client.put(url, content=b"GIF89a....", headers={"Content-Type": "image/png"}).status_code == 400
    assert client.put(url, content=_png(), headers={"Content-Type": "image/jpeg"}).status_code == 415
    r = client.put(url, content=_png(), headers={"Content-Type": "image/png"})
    assert r.status_code == 200, r.text
    assert r.json()["frame_png"] == f"notes/studio/frames/{a['id']}.png"
    assert (settings.frames_dir / f"{a['id']}.png").read_bytes()[:4] == b"\x89PNG"
    assert client.get(f"/media/notes/studio/frames/{a['id']}.png").status_code == 200
    assert client.put("/api/annotations/a-20990101-000000-ffff/frame", content=_png(),
                      headers={"Content-Type": "image/png"}).status_code == 404
    # deleting the annotation removes its still
    client.delete(f"/api/annotations/{a['id']}")
    assert not (settings.frames_dir / f"{a['id']}.png").exists()


def test_timelines_persist(client, settings):
    r = client.put("/api/timelines/swap-s02", json={"note": "v01 with s02 from v02",
                                                    "shots": {"s01": "v01_1080", "s02": "v02_1080"}})
    assert r.status_code == 200, r.text
    t = json.loads((settings.timelines_dir / "swap-s02.json").read_text())
    assert t["shots"]["s02"] == {"source": "v02_1080", "tier": "final",
                                 "path": "renders/snapshots/v02_1080/shots/s02_final.mov"}
    assert client.get("/api/timelines").json()["timelines"][0]["shots"] == {"s01": "v01_1080", "s02": "v02_1080"}
    assert client.put("/api/timelines/..%2fx", json={"shots": {}}).status_code in (400, 404, 405)
    assert client.put("/api/timelines/bad", json={"shots": {"s04": "live"}}).status_code == 400
    assert client.delete("/api/timelines/swap-s02").status_code == 200


def test_direct_store_frame_from_time(settings):
    st = Annotations(settings, Catalog(settings))
    a = st.create(AnnotationIn(version="v01", time=3.9999), "x")
    assert a["frame"] == 119 and a["shot"] == "s04"


def _post_raw(client, body: str):
    # json.loads (and so the API) accepts the NaN / Infinity literals that JSON itself does not have
    return client.post("/api/annotations", content=body, headers={"Content-Type": "application/json"})


@pytest.mark.parametrize("points", [
    "[[0.1,0.2,NaN]]", "[[0.1,0.2,Infinity]]", "[[0.1,0.2,-Infinity]]",     # pressure
    "[[NaN,0.2,0.5]]", "[[0.1,Infinity]]",                                  # x / y
    "[[0.1,0.2,1.5]]", "[[0.1,0.2,-0.1]]",                                  # pressure is 0..1
])
def test_non_finite_point_components_are_rejected(client, settings, points):
    r = _post_raw(client, '{"version":"v01","time":1,"drawing":{"shapes":[{"type":"pen","points":%s}]}}' % points)
    assert r.status_code == 422, r.text
    assert not settings.annotations_file.exists()
    assert client.get("/api/annotations").status_code == 200


@pytest.mark.parametrize("body", [
    '{"version":"v01","time":NaN}',
    '{"version":"v01","time":1,"drawing":{"aspect":NaN,"shapes":[]}}',
    '{"version":"v01","time":1,"drawing":{"shapes":[{"type":"arrow","size":NaN,"a":[0,0],"b":[1,1]}]}}',
    '{"version":"v01","time":1,"drawing":{"shapes":[{"type":"arrow","a":[0,NaN],"b":[1,1]}]}}',
])
def test_non_finite_numbers_anywhere_are_rejected(client, settings, body):
    assert _post_raw(client, body).status_code == 422
    assert not settings.annotations_file.exists()


def test_store_and_writer_never_emit_nan(settings, tmp_path):
    with pytest.raises(ValidationError):
        AnnotationIn(version="v01", time=1, drawing={"shapes": [{"type": "pen", "points": [[0.1, 0.2, math.nan]]}]})
    # the JSON writer refuses non-finite numbers outright and leaves the old file in place
    p = tmp_path / "writer" / "x.json"
    atomic_write_json(p, {"ok": 1})
    for bad in (math.nan, math.inf, -math.inf, [[0.1, math.nan]], {"a": [math.inf]}):
        with pytest.raises(ValueError):
            atomic_write_json(p, {"x": bad})
    assert json.loads(p.read_text()) == {"ok": 1}
    assert [x.name for x in p.parent.iterdir()] == ["x.json"]                # no temp files left behind


def test_patch_validates_shot_like_create(client, settings):
    a = client.post("/api/annotations", json={"version": "v01", "time": 1.0}).json()
    assert client.patch(f"/api/annotations/{a['id']}", json={"shot": "s77"}).status_code == 400
    assert client.get("/api/annotations").json()["annotations"][0]["shot"] == "s02"   # unchanged on disk
    r = client.patch(f"/api/annotations/{a['id']}", json={"shot": "s03"})
    assert r.status_code == 200 and r.json()["shot"] == "s03"


def test_names_ids_and_colours_must_match_whole_string(client, settings):
    assert valid_name("ok-1.x") and not valid_name("x\n") and not valid_name("x\ny")
    assert client.put("/api/timelines/x%0A", json={"shots": {}}).status_code == 400
    assert not (settings.timelines_dir / "x\n.json").exists()
    bad = {"shapes": [{"type": "arrow", "color": "#ffffff\n", "a": [0, 0], "b": [1, 1]}]}
    assert client.post("/api/annotations", json={"version": "v01", "time": 1, "drawing": bad}).status_code == 422
    assert ID_RE.fullmatch("a-20261005-020302-3aee") and not ID_RE.fullmatch("a-20261005-020302-3aee\n")
    st = Annotations(settings, Catalog(settings))
    with pytest.raises(StoreError):
        st.save_frame("a-20261005-020302-3aee\n", _png())
    assert client.post("/api/live/freeze", json={"name": "v07\n"}).status_code == 400


def _studio_cut(project, tiny_mp4, name="part", **meta):
    (project / "renders/studio/cuts").mkdir(parents=True, exist_ok=True)
    (project / f"renders/studio/cuts/{name}.mp4").write_bytes(tiny_mp4)
    (project / f"renders/studio/cuts/{name}.json").write_text(json.dumps({"name": name, **meta}))
    return f"renders/studio/cuts/{name}.mp4"


def test_notes_on_a_partial_cut_store_film_frame_and_shot(client, project, tiny_mp4):
    # built before builds recorded their film range: located from its from/to shots (s02 30-75, s03 75-105)
    path = _studio_cut(project, tiny_mp4, **{"from": "s02", "to": "s03"})
    v = {x["id"]: x for x in client.get("/api/versions").json()["versions"]}["studio-part"]
    assert (v["cut"]["film_f0"], v["cut"]["film_f1"]) == (30, 105)
    # the browser sends film frames; frame 80 is cut-local frame 50, and belongs to s03
    r = client.post("/api/annotations", json={"version": "studio-part", "cut": path, "time": 80 / 30, "frame": 80})
    assert r.status_code == 200, r.text
    a = r.json()
    assert (a["frame"], a["shot"], a["time"]) == (80, "s03", round(80 / 30, 4))
    # frames the cut does not have are refused, at create and when moved
    for f in (0, 29, 105, 119):
        r = client.post("/api/annotations", json={"version": "studio-part", "cut": path, "time": f / 30, "frame": f})
        assert r.status_code == 400 and "outside" in r.json()["detail"], (f, r.text)
    assert client.patch(f"/api/annotations/{a['id']}", json={"frame": 10}).status_code == 400
    assert client.patch(f"/api/annotations/{a['id']}", json={"frame": 31}).json()["shot"] == "s02"
    # with no cut given, the version's own cut is the one checked
    assert client.post("/api/annotations", json={"version": "studio-part", "time": 0.1}).status_code == 400
    # a cut path that is not this version's is refused
    r = client.post("/api/annotations", json={"version": "v01", "cut": path, "time": 1.2, "frame": 36})
    assert r.status_code == 400
    # full cuts cover the whole film
    assert client.post("/api/annotations", json={"version": "v01", "time": 0.1}).status_code == 200


def test_recorded_film_range_wins_over_the_edl(client, project, tiny_mp4):
    _studio_cut(project, tiny_mp4, name="rec", film_f0=75, film_f1=120, **{"from": "s03", "to": "s04"})
    _studio_cut(project, tiny_mp4, name="full")
    vs = {x["id"]: x for x in client.get("/api/versions").json()["versions"]}
    assert (vs["studio-rec"]["cut"]["film_f0"], vs["studio-rec"]["cut"]["film_f1"]) == (75, 120)
    assert (vs["studio-full"]["cut"]["film_f0"], vs["studio-full"]["cut"]["film_f1"]) == (0, None)
    assert (vs["v01"]["cut"]["film_f0"], vs["v01"]["cut"]["film_f1"]) == (0, None)
