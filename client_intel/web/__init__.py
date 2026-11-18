"""Loopback web adapter over the shared client briefing service."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, closing
from pathlib import Path
import re
import sqlite3

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..diagnostics import readiness
from ..jobs import Jobs, QueueFull
from ..planning import Request, RequestError, open_snapshot, resolve_client
from ..run import brief

STATIC = Path(__file__).parent / "static"
ARTIFACTS = {
    "briefing.md": "text/markdown",
    "evidence.json": "application/json",
    "verification.json": "application/json",
    "trace.jsonl": "application/x-ndjson",
    "manifest.json": "application/json",
}
RUN_ID = re.compile(r"[0-9a-f]{32}")


class RunInput(BaseModel):
    client: str = Field(min_length=1, max_length=200)
    trigger: str = Field(min_length=1, max_length=96)
    rep_context: str = Field(default="", max_length=2000)


def create_app(data_dir: Path, output_dir: Path, state_dir: Path, config: dict, env: dict[str, str]) -> FastAPI:
    output_dir = output_dir.resolve()
    jobs = Jobs(state_dir.resolve())

    @asynccontextmanager
    async def lifespan(app):
        jobs.initialise()
        app.state.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="briefing")
        try:
            yield
        finally:
            app.state.pool.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Client intelligence", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")

    def public_run(run_id: str) -> dict:
        row = jobs.get(run_id) if RUN_ID.fullmatch(run_id) else None
        if not row:
            raise HTTPException(404, "Run not found.")
        directory = row.pop("run_directory")
        row["artifacts"] = {name: f"/api/runs/{run_id}/artifacts/{name}" for name in ARTIFACTS} if directory else {}
        return row

    def execute(run_id: str, request: Request):
        if not jobs.start(run_id):
            return
        try:
            result = brief(request, data_dir, output_dir, config, env,
                           progress=lambda stage: jobs.progress(run_id, stage))
        except Exception:
            jobs.finish(run_id, "failed", error="The run could not be saved. Check local source and output settings.")
        else:
            jobs.finish(run_id, "completed", outcome=result.status, run_directory=result.path.name)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", media_type="text/html", headers={
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'self'",
        })

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        state = readiness(data_dir, output_dir, state_dir, env)
        return JSONResponse(state, status_code=200 if state["ready"] else 503)

    @app.get("/api/clients")
    def clients():
        try:
            with closing(open_snapshot(data_dir)) as db:
                return [dict(row) for row in db.execute("SELECT client_id, client_name FROM clients ORDER BY client_name")]
        except (RequestError, OSError, sqlite3.Error):
            raise HTTPException(503, "The starter snapshot is unavailable.") from None

    @app.get("/api/triggers")
    def triggers():
        return [{"id": key, "label": value["label"]} for key, value in config["triggers"].items()]

    @app.post("/api/runs", status_code=202)
    def create_run(input: RunInput):
        if input.trigger not in config["triggers"]:
            raise HTTPException(422, "Choose a configured trigger.")
        credential = env.get("OPENAI_API_KEY", "").strip()
        if credential and any(credential in text for text in (input.client, input.rep_context)):
            raise HTTPException(422, "Remove the configured API credential from the request.")
        try:
            db = open_snapshot(data_dir)
        except (RequestError, OSError, sqlite3.Error):
            raise HTTPException(503, "The starter snapshot is unavailable.") from None
        with closing(db):
            try:
                account = resolve_client(db, input.client)
            except RequestError as exc:
                raise HTTPException(422, str(exc)) from None
            except sqlite3.Error:
                raise HTTPException(503, "The starter snapshot is unavailable.") from None
        request = Request(account["client_id"], input.trigger, input.rep_context)
        try:
            run_id = jobs.enqueue(request)
        except QueueFull as exc:
            raise HTTPException(429, str(exc)) from None
        try:
            app.state.pool.submit(execute, run_id, request)
        except RuntimeError:
            jobs.finish(run_id, "interrupted", error="The server is stopping.")
        return public_run(run_id)

    @app.get("/api/runs/{run_id}")
    def run_status(run_id: str):
        return JSONResponse(public_run(run_id), headers={"Cache-Control": "no-store"})

    @app.get("/api/runs/{run_id}/artifacts/{name}")
    def artifact(run_id: str, name: str):
        row = jobs.get(run_id) if RUN_ID.fullmatch(run_id) and name in ARTIFACTS else None
        if row is None or not row["run_directory"]:
            raise HTTPException(404, "Artifact not found.")
        directory = (output_dir / row["run_directory"]).resolve()
        path = (directory / name).resolve()
        if directory.parent != output_dir or path.parent != directory or not path.is_file():
            raise HTTPException(404, "Artifact not found.")
        return FileResponse(path, media_type=ARTIFACTS[name], filename=name,
                            headers={"Cache-Control": "no-store"})

    return app
