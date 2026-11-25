import os
import time


def test_stale_proxy_is_never_served_after_a_failed_regeneration(client, project, tiny_mp4):
    src = project / "renders/shots/s01_cycle.mov"
    src.write_bytes(tiny_mp4)
    pm = client.app.state.proxies
    pm.start()                                        # the on-demand worker only (warm-up is off in tests)
    try:
        r = client.get("/api/thumb/live/s01.jpg")
        assert r.status_code == 200 and r.content[:2] == b"\xff\xd8", r.text
        old = r.content
        # the live master is rewritten into something ffmpeg can't read: the old thumbnail is now stale
        src.write_bytes(b"half-written render")
        os.utime(src, (time.time() + 5, time.time() + 5))
        for _ in range(2):                            # the retry is refused for the same input, still not stale
            r = client.get("/api/thumb/live/s01.jpg")
            assert r.status_code == 500 and r.json() == {"status": "failed"}, r.status_code
            assert r.content != old
        assert pm.stats["failed"] >= 1
        # a good render again: regenerated and served
        src.write_bytes(tiny_mp4)
        os.utime(src, (time.time() + 10, time.time() + 10))
        r = client.get("/api/thumb/live/s01.jpg")
        assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    finally:
        pm.stop()

