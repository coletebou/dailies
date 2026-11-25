from pathlib import Path

import pytest

from dailies_server.safepath import PathDenied, PathMissing, resolve_media


@pytest.mark.parametrize("rel", [
    "build/edl.json",
    "music/lyrics_timed.json",
    "notes/critiques/v01_critic-flash.json",
    "renders/ok.txt",
    "renders/snapshots/v01_1080/shots/s01_final.mov",
])
def test_allowed(project: Path, rel: str):
    assert resolve_media(project, rel).is_file()


@pytest.mark.parametrize("rel", [
    "tools/secret.py",                      # not allowlisted
    "notes/private.md",                     # notes/ is not, only notes/critiques and notes/studio
    "music/mix_master.wav",                 # music/ only lyrics*.json
    "build/other.json",
    "renders/../tools/secret.py",           # traversal
    "renders/./ok.txt",
    "renders//ok.txt",
    "/etc/passwd",
    "../outside.txt",
    "renders\\..\\tools\\secret.py",
    "renders/ok.txt\x00.jpg",
    "renders/escape.txt",                   # symlink inside renders/ pointing outside the project
    "renders/escape_dir/outside.txt",       # directory symlink escape
    "notes/critiques/../private.md",
    "",
])
def test_denied(project: Path, rel: str):
    with pytest.raises(PathDenied):
        resolve_media(project, rel)


def test_missing_inside_allowlist_is_404_not_403(project: Path):
    with pytest.raises(PathMissing):
        resolve_media(project, "renders/nope.mp4")
    with pytest.raises(PathMissing):
        resolve_media(project, "renders/snapshots")          # a directory, not a file


def test_media_http(client):
    assert client.get("/media/build/edl.json").status_code == 200
    assert client.get("/media/tools/secret.py").status_code == 403
    assert client.get("/media/renders/escape.txt").status_code == 403
    assert client.get("/media/renders/%2e%2e/tools/secret.py").status_code == 403
    assert client.get("/media/renders/..%2ftools/secret.py").status_code in (403, 404)
    assert client.get("/media/renders/nope.mp4").status_code == 404


def test_range_requests(client, project: Path):
    (project / "renders/clip.mp4").write_bytes(bytes(range(256)) * 4)
    r = client.get("/media/renders/clip.mp4", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206
    assert r.headers["content-range"] == "bytes 10-19/1024"
    assert r.content == bytes(range(10, 20))
    assert r.headers["accept-ranges"] == "bytes"
    assert "private" in r.headers["cache-control"]
    r = client.get("/media/renders/clip.mp4", headers={"Range": "bytes=2000-"})
    assert r.status_code == 416


def test_spa_does_not_escape_dist(client, project: Path):
    (project / "web-dist").mkdir()
    (project / "web-dist/index.html").write_text("<html>studio</html>")
    assert client.get("/").text == "<html>studio</html>"
    assert client.get("/compare").text == "<html>studio</html>"   # SPA fallback
    r = client.get("/..%2ftools/secret.py")
    assert "SECRET" not in r.text
    assert client.get("/api/nope").status_code == 404


def test_versions_catalog(client):
    vs = {v["id"]: v for v in client.get("/api/versions").json()["versions"]}
    assert set(vs) == {"v01", "v02", "v03"}
    assert vs["v01"]["cut"]["path"] == "renders/snapshots/v01_1080_fixed.mp4"
    assert [a["path"] for a in vs["v01"]["alternates"]] == []                 # _1080 and _crit superseded by _fixed
    assert vs["v01"]["critiques"][0]["weakest"][0]["t"] == 2.5                 # "00:02" = mid-second 2
    assert vs["v01"]["sources"] == ["v01_1080"]
    assert vs["v03"]["pending"] is True
    assert vs["v02"]["cut"]["url"].startswith("/media/renders/previews/v02.mp4?v=")
