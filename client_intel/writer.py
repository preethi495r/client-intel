"""Single structured writer, backed by the OpenAI Responses API."""

from dataclasses import dataclass, field
from pathlib import Path
import json
import socket
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import Request as HTTPRequest, urlopen

from .models import Evidence

PROMPT_PATH = Path(__file__).parent / "prompts" / "writer.txt"
RESPONSE_URL = "https://api.openai.com/v1/responses"


class WriterError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelSettings:
    model: str
    key: str = field(repr=False)
    timeout: float

    @classmethod
    def from_env(cls, env: dict[str, str]):
        if env.get("CLIENT_INTEL_MODEL_PROVIDER", "openai") != "openai":
            raise WriterError("CLIENT_INTEL_MODEL_PROVIDER must be openai.")
        model = env.get("CLIENT_INTEL_MODEL", "gpt-6.2-sol").strip()
        key = env.get("OPENAI_API_KEY", "").strip()
        if not key or key == "replace-with-your-openai-api-key":
            raise WriterError("Set OPENAI_API_KEY in .env or the process environment.")
        try:
            timeout = float(env.get("CLIENT_INTEL_MODEL_TIMEOUT", "240"))
        except ValueError:
            raise WriterError("Give a numeric model timeout in seconds.") from None
        if not model or not 0 < timeout <= 900:
            raise WriterError("Give a model name and a timeout between zero and 900 seconds.")
        return cls(model, key, timeout)


def candidates(evidence: list[Evidence], trigger_config: dict) -> dict[str, Evidence]:
    order = {analysis: index for index, analysis in
             enumerate(trigger_config["core"] + trigger_config["support"])}
    visible = sorted((item for item in evidence if item.model_visible and item.sources),
                     key=lambda item: (item.priority, order.get(item.analysis, 100), item.key))
    facts = [item for item in visible if item.analysis != "data_quality"]
    gaps = [item for item in visible if item.analysis == "data_quality"]
    return ({f"F{index:02d}": item for index, item in enumerate(facts, 1)} |
            {f"G{index:02d}": item for index, item in enumerate(gaps, 1)})


def _output_schema(options: list) -> dict:
    facts = [alias for alias, analysis, _ in options if analysis != "data_quality"]
    gaps = [alias for alias, analysis, _ in options if analysis == "data_quality"]

    def selection(aliases, maximum, minimum=0):
        item = {"type": "string", "enum": aliases} if aliases else {"type": "string"}
        return {"type": "array", "items": item, "minItems": minimum, "maxItems": maximum if aliases else 0}

    return {
        "type": "object",
        "properties": {
            "lead": selection(facts, 3),
            "context": selection(facts, 4),
            "actions": {"type": "array", "maxItems": 2 if facts or gaps else 0, "items": {
                "type": "object",
                "properties": {"action": {"type": "string", "maxLength": 230},
                               "why": selection(facts + gaps, 3, 1 if facts or gaps else 0),
                               "readiness": {"type": "string", "enum": ["ready_for_discussion", "needs_validation"]}},
                "required": ["action", "why", "readiness"],
                "additionalProperties": False,
            }},
            "gaps": selection(gaps, 3),
        },
        "required": ["lead", "context", "actions", "gaps"],
        "additionalProperties": False,
    }


def write(settings: ModelSettings, prompt: str, payload: dict) -> tuple[dict, dict]:
    body = {
        "model": settings.model,
        "instructions": prompt,
        "input": json.dumps(payload, ensure_ascii=False),
        "store": False,
        "reasoning": {"effort": "low"},
        "max_output_tokens": 1500,
        "text": {"format": {"type": "json_schema",
            "name": "sales_briefing", "strict": True, "schema": _output_schema(payload["fact_options"]),
        }},
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {settings.key}"}
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    if settings.key.encode("utf-8") in encoded:
        raise WriterError("The writer input contains the configured API credential.")
    request = HTTPRequest(RESPONSE_URL, data=encoded, headers=headers)
    start = perf_counter()
    try:
        with urlopen(request, timeout=settings.timeout) as response:
            result = json.load(response)
    except HTTPError as exc:
        raise WriterError(f"The model endpoint returned HTTP {exc.code}.") from None
    except (URLError, OSError, socket.timeout):
        raise WriterError("The model endpoint was unreachable or timed out.") from None
    except (ValueError, UnicodeError):
        raise WriterError("The model endpoint returned invalid JSON.") from None
    try:
        if result["status"] != "completed":
            raise WriterError("The model did not finish its structured response.")
        response_text = "".join(part["text"] for item in result["output"] if item["type"] == "message"
                                for part in item["content"] if part["type"] == "output_text")
        draft = json.loads(response_text)
    except (KeyError, IndexError, TypeError, ValueError):
        raise WriterError("The writer response did not contain a JSON draft.") from None
    info = {"provider": "openai", "model": settings.model,
            "reported_model": result.get("model"), "usage": result.get("usage"),
            "seconds": round(perf_counter() - start, 3)}
    if not isinstance(draft, dict):
        raise WriterError("The writer draft must be a JSON object.")
    return draft, info
