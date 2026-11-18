"""Local source, storage and model configuration checks without an API call."""

from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryFile

from .planning import RequestError, open_snapshot
from .writer import ModelSettings, WriterError, write


def readiness(data_dir: Path, output_dir: Path, state_dir: Path, env: dict[str, str]) -> dict:
    checks = {}
    try:
        with closing(open_snapshot(data_dir)) as db:
            db.execute("SELECT client_id FROM clients LIMIT 1").fetchone()
        checks["snapshot"] = "ok"
    except (RequestError, sqlite3.Error):
        checks["snapshot"] = "The starter snapshot could not be opened."
    for name, directory in (("outputs", output_dir), ("state", state_dir)):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with TemporaryFile(dir=directory) as handle:
                handle.write(b"ready")
            checks[name] = "ok"
        except OSError:
            checks[name] = "The storage directory is unwritable."
    try:
        ModelSettings.from_env(env)
        checks["model_configuration"] = "ok"
    except WriterError as exc:
        checks["model_configuration"] = str(exc)
    return {"ready": all(status == "ok" for status in checks.values()), "checks": checks}


def model_smoke(env: dict[str, str]) -> dict:
    try:
        settings = ModelSettings.from_env(env)
        _, info = write(settings, "Return empty arrays for lead, context, actions and gaps.", {"fact_options": []})
    except WriterError as exc:
        return {"ok": False, "reason": str(exc)}
    return {"ok": True, "model": info["reported_model"], "seconds": info["seconds"]}
