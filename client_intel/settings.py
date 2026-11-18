"""Resolve documented settings from the launch directory and process environment."""

from pathlib import Path
import os
import re


def environment() -> dict[str, str]:
    settings = {}
    override = os.environ.get("CLIENT_INTEL_ENV_FILE")
    path = Path(override) if override else Path.cwd() / ".env"
    if override and not path.is_file():
        raise ValueError("CLIENT_INTEL_ENV_FILE must point to an existing .env file.")
    if path.is_file():
        for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            line = line.removeprefix("export ")
            key, equals, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not equals or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError(f"Expected NAME=value at .env line {number}.")
            if value.startswith(("'", '"')):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError(f"Unclosed quoted setting at .env line {number}.")
                value = value[1:-1]
            if key.startswith("CLIENT_INTEL_") or key == "OPENAI_API_KEY":
                settings[key] = value
    settings.update((key, value) for key, value in os.environ.items()
                    if key.startswith("CLIENT_INTEL_") or key == "OPENAI_API_KEY")
    return settings
