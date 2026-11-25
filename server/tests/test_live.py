import os
import time
from urllib.parse import parse_qs, urlsplit

from dailies_server.catalog import Catalog


def test_live_scan_newest_first_and_filters(client, project):
    for i, name in enumerate(["s01_cycle_preview.mp4", "s02_cycle_sheet.jpg", "s02_cycle_preview.part00001.mp4"]):
        p = project / "renders/shots" / name
        p.write_bytes(b"x")
        os.utime(p, (time.time() - 100 + i, time.time() - 100 + i))
    (project / "renders/tests/qa").mkdir(parents=True)
    (project / "renders/tests/qa/s03_cycle_f0001.jpg").write_bytes(b"x")
    (project / "renders/snapshots/v02_1080/parts").mkdir()
    (project / "renders/snapshots/v02_1080/parts/s04_final_r1-60.mov").write_bytes(b"x")
    # Pin the order: Linux stamps mtimes from a coarse clock, so two files written back to back can tie.
    now = time.time()
    os.utime(project / "renders/tests/qa/s03_cycle_f0001.jpg", (now - 2, now - 2))
    os.utime(project / "renders/snapshots/v02_1080/parts/s04_final_r1-60.mov", (now - 1, now - 1))
    items = client.get("/api/live").json()["items"]
    names = [i["name"] for i in items]
    assert "s02_cycle_preview.part00001.mp4" not in names                     # chunk temps are not previews
    assert names[:2] == ["s04_final_r1-60.mov", "s03_cycle_f0001.jpg"]        # newest first
    part = items[0]
    assert part["type"] == "part" and part["version"] == "v02_1080" and part["range"] == [1, 60] and part["sid"] == "s04"
    assert part["thumb"].startswith("/api/live/thumb?path=renders/snapshots/v02_1080/parts/")
    assert [i["name"] for i in client.get("/api/live?shot=s02").json()["items"]] == ["s02_cycle_sheet.jpg"]
    assert {i["type"] for i in client.get("/api/live?types=qa").json()["items"]} == {"qa"}
    # stills only for parts and previews; nothing else can be fed to ffmpeg through this endpoint
    assert client.get("/api/live/thumb?path=build/edl.json").status_code == 404
    assert client.get("/api/live/thumb?path=renders/ok.txt").status_code == 404
    assert client.get("/api/live/thumb?path=../outside.txt").status_code == 404


def test_media_and_live_urls_escape_file_names(client, project, tiny_mp4):
    odd = "s01_cycle #2&x+y%z_preview.mp4"
    (project / "renders/shots" / odd).write_bytes(b"odd preview")
    it = next(i for i in client.get("/api/live").json()["items"] if i["name"] == odd)
    assert it["url"].startswith("/media/renders/shots/s01_cycle%20%232%26x%2By%25z_preview.mp4?v=")
    r = client.get(it["url"])
    assert r.status_code == 200 and r.content == b"odd preview"
    q = parse_qs(urlsplit(it["thumb"]).query)
    assert q["path"] == [f"renders/shots/{odd}"] and list(q) == ["path", "v"]
    # cuts too: the /media URL and the cut-proxy id
    (project / "renders/previews/v04_a&b #1.mp4").write_bytes(tiny_mp4)
    v = {x["id"]: x for x in client.get("/api/versions").json()["versions"]}["v04"]
    assert v["cut"]["url"].startswith("/media/renders/previews/v04_a%26b%20%231.mp4?v=")
    assert client.get(v["cut"]["url"]).status_code == 200
    assert "%26b%20%231" in v["cut"]["proxy_url"]
    assert client.get(v["cut"]["proxy_url"]).status_code == 503              # found and queued, not 404


def test_freeze_clones_live_shots_into_a_new_source(client, settings, project):
    r = client.post("/api/live/freeze", json={"name": "v07"})
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "v07", "source": "frozen-v07", "shots": 3}
    frozen = settings.frozen_dir / "v07/shots"
    assert sorted(p.name for p in frozen.iterdir()) == ["s01_cycle.mov", "s02_cycle.mov", "s03_cycle.mov"]
    assert (frozen / "s03_cycle.mov").read_text() == "live:s03"
    # a later in-place rewrite of the live render does not reach the frozen copy
    (project / "renders/shots/s03_cycle.mov").write_text("live:s03:next-cycle")
    assert (frozen / "s03_cycle.mov").read_text() == "live:s03"
    src = Catalog(settings).source("frozen-v07")
    assert src.version == "v07" and set(src.files) == {"s01", "s02", "s03"}
    assert client.post("/api/live/freeze", json={"name": "v07"}).status_code == 400   # never overwrites
    assert client.post("/api/live/freeze", json={"name": "../x"}).status_code == 400
