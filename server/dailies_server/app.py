"""FastAPI app: JSON API, allowlisted media with Range support, lazy proxies, and the built frontend."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response

from .auth import AccessVerifier, GuardMiddleware
from .catalog import Catalog
from .config import Settings
from .jobs import BuildError, BuildManager
from .live import LiveScanner
from .proxies import ProxyManager
from .safepath import PathDenied, PathMissing, resolve_media
from .store import AnnotationIn, AnnotationPatch, Annotations, StoreError, TimelineIn, Timelines
from .util import SID_RE, valid_name

log = logging.getLogger("studio")
MEDIA_CACHE = "private, max-age=86400"
STARTED = time.time()


def _user(request: Request) -> str:
    return getattr(request.state, "user", "local")


def create_app(settings: Settings | None = None, verifier: AccessVerifier | None = None,
               start_workers: bool = True) -> FastAPI:
    s = settings or Settings()
    cat = Catalog(s)
    proxies = ProxyManager(s, cat)
    builds = BuildManager(s, cat)
    live = LiveScanner(s, cat)
    notes = Annotations(s, cat)
    timelines = Timelines(s, cat)

    @asynccontextmanager
    async def lifespan(_app):
        if start_workers:
            proxies.start()
            builds.start()
        log.info("dailies up: root=%s warm=%s", s.root, s.warm)
        yield
        proxies.stop()
        builds.stop()

    app = FastAPI(title="Dailies", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.catalog, app.state.proxies = s, cat, proxies
    app.state.builds, app.state.live, app.state.notes, app.state.timelines = builds, live, notes, timelines
    app.add_middleware(GuardMiddleware, settings=s, verifier=verifier or AccessVerifier(s))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, exc: RequestValidationError):
        # FastAPI's default 422 echoes each bad input, and json.loads lets NaN / Infinity into a body: rejecting one
        # would then fail to serialise and turn into a 500. Where and why is all the client needs.
        errors = [{k: v for k, v in e.items() if k != "input"} for e in exc.errors()]
        return JSONResponse({"detail": jsonable_encoder(errors)}, status_code=422)

    def media_response(p: Path) -> Response:
        # "private": never cached at the Cloudflare edge, only in the viewer's own browser
        return FileResponse(p, headers={"Cache-Control": MEDIA_CACHE})

    def no_store(data, status: int = 200) -> JSONResponse:
        return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})

    # --- read API ------------------------------------------------------------------------------------------------------
    @app.get("/api/health")
    def health():
        jobs = builds.list(10)
        return no_store({"ok": True, "root": str(s.root), "uptime_s": round(time.time() - STARTED),
                         "versions": len(cat.versions()), "sources": len(cat.sources()),
                         "builds_running": sum(j["status"] == "running" for j in jobs),
                         "builds_queued": sum(j["status"] == "queued" for j in jobs),
                         "proxies": proxies.stats, "web_built": (s.web_dist / "index.html").is_file()})

    @app.get("/api/meta")
    def meta():
        return no_store(cat.meta())

    def proxied_cut(c: dict) -> dict:
        t = proxies.cut_task(c)
        return {**c, "proxy_ready": proxies.fresh(t),
                "proxy_url": f"/api/cut-proxy/{quote(c['id'])}.mp4?v={proxies.url_version(t)}"}

    @app.get("/api/versions")
    def versions():
        out = []
        for v in cat.versions(builds.active_names()):
            v = dict(v)
            if not v.get("pending"):
                v["cut"] = proxied_cut(v["cut"])
                v["alternates"] = [proxied_cut(c) for c in v["alternates"]]
            out.append(v)
        return no_store({"versions": out})

    @app.get("/api/critiques")
    def critiques():
        return no_store({"critiques": cat.critiques()})

    @app.get("/api/sources")
    def sources():
        srcs = cat.sources()
        shots: dict[str, list] = {sid: [] for sid in cat.shot_ids()}
        for src in srcs:
            for sid, f in src.files.items():
                tt, pt = proxies.shot_task("thumb", src.id, sid), proxies.shot_task("proxy", src.id, sid)
                st = f.path.stat()
                shots[sid].append({"source": src.id, "version": src.version, "tier": f.tier, "name": f.path.name,
                                   "mtime": st.st_mtime, "size": st.st_size,
                                   "thumb": f"/api/thumb/{src.id}/{sid}.jpg?v={proxies.url_version(tt)}",
                                   "thumb_ready": proxies.fresh(tt),
                                   "proxy": f"/api/proxy/{src.id}/{sid}.mp4?v={proxies.url_version(pt)}",
                                   "proxy_ready": proxies.fresh(pt)})
        return no_store({"sources": [{"id": x.id, "label": x.label, "kind": x.kind, "version": x.version,
                                      "count": len(x.files)} for x in srcs], "shots": shots})

    # --- media ---------------------------------------------------------------------------------------------------------
    @app.get("/media/{rel:path}")
    def media(rel: str, request: Request):
        try:
            p = resolve_media(s.root, rel)
        except PathDenied:
            raise HTTPException(403, "not allowed") from None
        except PathMissing:
            raise HTTPException(404, "not found") from None
        return media_response(p)

    async def lazy(task, request: Request, wait_s: float, prio: int):
        if task is None:
            raise HTTPException(404, "no such shot in that source")
        if not proxies.fresh(task):
            done = proxies.request(task, prio)
            await asyncio.to_thread(done.wait, wait_s)
            # an older output may still exist: serve only one made from the source as it is now
            if not proxies.fresh(task):
                if proxies.failed(task):
                    return JSONResponse({"status": "failed"}, status_code=500)
                return JSONResponse({"status": "generating"}, status_code=503, headers={"Retry-After": "5"})
        return media_response(task.out)

    def _check_ids(source: str, sid: str) -> None:
        if not valid_name(source) or not SID_RE.fullmatch(sid):
            raise HTTPException(404, "bad id")

    @app.get("/api/thumb/{source}/{sid}.jpg")
    async def thumb(source: str, sid: str, request: Request):
        _check_ids(source, sid)
        return await lazy(proxies.shot_task("thumb", source, sid), request, 40, 0)

    @app.get("/api/proxy/{source}/{sid}.mp4")
    async def proxy(source: str, sid: str, request: Request):
        _check_ids(source, sid)
        return await lazy(proxies.shot_task("proxy", source, sid), request, 120, 1)

    @app.post("/api/proxies/ensure")
    def ensure(body: dict = Body(...)):
        source, sid, kind = str(body.get("source", "")), str(body.get("sid", "")), body.get("kind", "proxy")
        _check_ids(source, sid)
        t = proxies.shot_task("thumb" if kind == "thumb" else "proxy", source, sid)
        if not t:
            raise HTTPException(404, "no such shot in that source")
        if proxies.fresh(t):
            return {"ready": True}
        proxies.request(t, 1)
        return {"ready": False}

    @app.get("/api/cut-proxy/{cut_id}.mp4")
    async def cut_proxy(cut_id: str, request: Request):
        c = cat.cut_by_id(cut_id)
        if not c:
            raise HTTPException(404, "unknown cut")
        t = proxies.cut_task(c)
        if proxies.fresh(t):
            return media_response(t.out)
        proxies.request(t, 2)
        return JSONResponse({"status": "generating"}, status_code=503, headers={"Retry-After": "30"})

    @app.get("/api/audio/mix.m4a")
    async def audio(request: Request):
        t = proxies.audio_task()
        if not t.src.is_file():
            raise HTTPException(404, "no mix")
        return await lazy(t, request, 60, 0)

    # --- live ----------------------------------------------------------------------------------------------------------
    @app.get("/api/live")
    def live_items(since: float | None = None, shot: str | None = None, types: str | None = None,
                   limit: int = Query(200, ge=1, le=1000)):
        ts = {t for t in (types or "").split(",") if t} or None
        return no_store(live.query(since, shot, ts, limit))

    @app.get("/api/live/thumb")
    async def live_thumb(path: str, request: Request):
        p = live.thumb_source(path)
        if not p:
            raise HTTPException(404, "no still for that file")
        return await lazy(proxies.live_thumb_task(p), request, 30, 0)

    @app.post("/api/live/freeze")
    def freeze(request: Request, body: dict = Body(...)):
        try:
            return live.freeze(str(body.get("name", "")), _user(request))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    # --- annotations ---------------------------------------------------------------------------------------------------
    @app.get("/api/annotations")
    def list_annotations():
        return no_store({"annotations": notes.list()})

    @app.post("/api/annotations")
    def create_annotation(body: AnnotationIn, request: Request):
        try:
            return notes.create(body, _user(request))
        except StoreError as e:
            raise HTTPException(400, str(e)) from None

    @app.patch("/api/annotations/{aid}")
    def patch_annotation(aid: str, body: AnnotationPatch):
        try:
            a = notes.update(aid, body)
        except StoreError as e:
            raise HTTPException(400, str(e)) from None
        if not a:
            raise HTTPException(404, "no such annotation")
        return a

    @app.delete("/api/annotations/{aid}")
    def delete_annotation(aid: str):
        if not notes.delete(aid):
            raise HTTPException(404, "no such annotation")
        return {"deleted": aid}

    @app.put("/api/annotations/{aid}/frame")
    async def put_frame(aid: str, request: Request):
        if request.headers.get("content-type", "").split(";")[0].strip() != "image/png":
            raise HTTPException(415, "send image/png")
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > 20 * 1024 * 1024:
                raise HTTPException(413, "png too large")
        try:
            return await asyncio.to_thread(notes.save_frame, aid, bytes(data))
        except KeyError:
            raise HTTPException(404, "no such annotation") from None
        except StoreError as e:
            raise HTTPException(400, str(e)) from None

    # --- timelines -----------------------------------------------------------------------------------------------------
    @app.get("/api/timelines")
    def list_timelines():
        return no_store({"timelines": timelines.list()})

    @app.get("/api/timelines/{name}")
    def get_timeline(name: str):
        try:
            t = timelines.get(name)
        except StoreError as e:
            raise HTTPException(400, str(e)) from None
        if not t:
            raise HTTPException(404, "no such timeline")
        return no_store(t)

    @app.put("/api/timelines/{name}")
    def put_timeline(name: str, body: TimelineIn):
        try:
            return timelines.put(name, body)
        except StoreError as e:
            raise HTTPException(400, str(e)) from None

    @app.delete("/api/timelines/{name}")
    def delete_timeline(name: str):
        try:
            ok = timelines.delete(name)
        except StoreError as e:
            raise HTTPException(400, str(e)) from None
        if not ok:
            raise HTTPException(404, "no such timeline")
        return {"deleted": name}

    # --- builds --------------------------------------------------------------------------------------------------------
    @app.get("/api/builds")
    def list_builds():
        return no_store({"builds": builds.list()})

    @app.get("/api/builds/{jid}")
    def get_build(jid: str):
        j = builds.get(jid)
        if not j:
            raise HTTPException(404, "no such build")
        return no_store({**j, "log": builds.log_tail(jid)})

    @app.post("/api/builds")
    def create_build(request: Request, body: dict = Body(...)):
        shots = body.get("shots")
        if not isinstance(shots, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in shots.items()):
            raise HTTPException(400, "shots must map shot id -> source id")
        try:
            return builds.submit(str(body.get("name", "")), shots, body.get("from") or None, body.get("to") or None,
                                 body.get("timeline") or None, _user(request))
        except BuildError as e:
            raise HTTPException(400, str(e)) from None

    @app.post("/api/builds/{jid}/cancel")
    def cancel_build(jid: str):
        j = builds.cancel(jid)
        if not j:
            raise HTTPException(404, "no such build")
        return j

    # --- frontend ------------------------------------------------------------------------------------------------------
    @app.get("/{path:path}")
    def spa(path: str):
        dist = s.web_dist
        if path.startswith(("api/", "media/")):
            raise HTTPException(404, "not found")
        if path:
            cand = (dist / path).resolve()
            if cand.is_file() and cand.is_relative_to(dist.resolve()):
                cache = "private, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
                return FileResponse(cand, headers={"Cache-Control": cache})
        index = dist / "index.html"
        if not index.is_file():
            return Response("Dailies frontend not built: cd web && npm install && npm run build", media_type="text/plain",
                            status_code=503)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app
