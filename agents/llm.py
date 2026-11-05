"""Provider-aware structured output calls shared by the analysis agents."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from langsmith import get_current_run_tree, traceable
from openai import OpenAI


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

# LangGraph/LangSmith accept both the current variable names and the older
# LANGCHAIN_* names. Normalize legacy settings without overwriting explicit
# LANGSMITH_* values so tracing is configured before the graph is imported.
for _new_name, _legacy_name in (
    ("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY"),
    ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"),
    ("LANGSMITH_PROJECT", "LANGCHAIN_PROJECT"),
    ("LANGSMITH_ENDPOINT", "LANGCHAIN_ENDPOINT"),
):
    if not os.getenv(_new_name) and os.getenv(_legacy_name):
        os.environ[_new_name] = os.environ[_legacy_name]

GROQ_MODELS = {
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
}
_DEFAULT_MODELS = {
    "openai": os.getenv("ANALYSIS_MODEL", "gpt-4.1-mini"),
    "gemini": "gemini-3.8-flash",
    "groq": "openai/gpt-oss-120b",
}
_CACHE: OrderedDict[str, dict[str, Any]] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_LIMIT = 256


def _resolve_settings(provider_settings: dict[str, Any] | None) -> dict[str, str]:
    if provider_settings is not None and not isinstance(provider_settings, dict):
        raise ValueError("Provider settings must be an object.")
    selected = provider_settings or {}
    provider = str(selected.get("provider", "openai")).strip().lower()
    if provider not in _DEFAULT_MODELS:
        raise ValueError("Choose OpenAI, Gemini, or Groq in Settings.")

    model = str(selected.get("model", _DEFAULT_MODELS[provider])).strip()
    if provider == "groq" and model not in GROQ_MODELS:
        raise ValueError("Choose a supported Groq model in Settings.")
    if provider != "groq":
        model = _DEFAULT_MODELS[provider]

    api_key = str(selected.get("api_key", "")).strip()
    if not api_key:
        raise RuntimeError(f"Add your {provider.title()} API key in the extension Settings.")
    if len(api_key) > 512:
        raise ValueError("The API key is too long.")
    return {"provider": provider, "model": model, "api_key": api_key}


def _trace_inputs_without_secrets(inputs: dict[str, Any]) -> dict[str, Any]:
    """Keep provider credentials out of LangSmith run inputs."""
    return {key: value for key, value in inputs.items() if key != "api_key"}


def _usage_counts(usage: Any) -> dict[str, int] | None:
    """Normalize token usage returned by OpenAI-compatible and Gemini APIs."""
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    total_tokens = usage.get("total_tokens")
    if input_tokens is None and output_tokens is None:
        return None
    normalized = {
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
    }
    normalized["total_tokens"] = int(
        total_tokens if total_tokens is not None else
        normalized["input_tokens"] + normalized["output_tokens"]
    )
    return normalized


def _record_usage(usage: Any) -> None:
    counts = _usage_counts(usage)
    run = get_current_run_tree()
    if counts and run is not None:
        run.set(usage_metadata=counts)


@traceable(
    run_type="llm",
    name="provider_model_call",
    process_inputs=_trace_inputs_without_secrets,
)
def _openai_compatible_result(
    provider: str,
    model: str,
    api_key: str,
    name: str,
    schema: dict[str, Any],
    instructions: str,
    payload_json: str,
) -> str:
    base_url = "https://api.groq.com/openai/v1" if provider == "groq" else None
    client = OpenAI(api_key=api_key, base_url=base_url)
    if provider == "openai":
        response = client.responses.create(
            model=model,
            instructions=instructions,
            input=payload_json,
            text={"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}},
        )
        _record_usage(response.usage.model_dump() if response.usage else None)
        return response.output_text or ""

    messages = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": payload_json},
    ]
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": name, "strict": True, "schema": schema},
        },
    )
    _record_usage(response.usage.model_dump() if response.usage else None)
    return response.choices[0].message.content or ""


@traceable(
    run_type="llm",
    name="provider_model_call",
    process_inputs=_trace_inputs_without_secrets,
)
def _gemini_result(
    model: str,
    api_key: str,
    schema: dict[str, Any],
    instructions: str,
    payload_json: str,
) -> str:
    response = requests.post(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json={
            "model": model,
            "input": payload_json,
            "system_instruction": instructions,
            "response_format": {"type": "text", "mime_type": "application/json", "schema": schema},
            "store": False,
        },
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    interaction = data.get("interaction", data)
    usage = interaction.get("usage_metadata", interaction.get("usage"))
    if usage is None:
        usage = data.get("usage_metadata", data.get("usage"))
    _record_usage(usage)
    direct = interaction.get("output_text")
    if isinstance(direct, str):
        return direct

    text_parts: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") in {"text", "text_output"} and isinstance(value.get("text"), str):
                text_parts.append(value["text"])
            for key in ("steps", "outputs", "content"):
                if key in value:
                    collect(value[key])
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(interaction)
    if text_parts:
        return "".join(text_parts)
    raise RuntimeError("Gemini returned no structured text output.")


def structured(
    name: str,
    schema: dict[str, Any],
    instructions: str,
    payload: dict[str, Any],
    provider_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call the selected provider and cache results without storing keys in cache keys."""
    settings = _resolve_settings(provider_settings)
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    schema_json = json.dumps(schema, ensure_ascii=False, sort_keys=True)
    secret_fingerprint = hashlib.sha256(settings["api_key"].encode("utf-8")).hexdigest()
    cache_key = hashlib.sha256(
        json.dumps(
            [settings["provider"], settings["model"], secret_fingerprint, name, schema_json,
             instructions, payload_json],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached is not None:
            _CACHE.move_to_end(cache_key)
            return copy.deepcopy(cached)

    if settings["provider"] == "gemini":
        output_text = _gemini_result(
            settings["model"], settings["api_key"], schema, instructions, payload_json,
            langsmith_extra={"metadata": {
                "ls_provider": "google_genai",
                "ls_model_name": settings["model"],
            }},
        )
    else:
        output_text = _openai_compatible_result(
            settings["provider"], settings["model"], settings["api_key"],
            name, schema, instructions, payload_json,
            langsmith_extra={"metadata": {
                "ls_provider": settings["provider"],
                "ls_model_name": settings["model"],
            }},
        )
    if not output_text.strip():
        raise RuntimeError(f"{name}: provider returned an empty response.")
    result = json.loads(output_text)
    _validate_schema(result, schema, name)

    with _CACHE_LOCK:
        _CACHE[cache_key] = copy.deepcopy(result)
        _CACHE.move_to_end(cache_key)
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)
    return result


def _validate_schema(value: Any, schema: dict[str, Any], path: str) -> None:
    """Validate the subset of JSON Schema used by the agents."""
    expected = schema.get("type")
    expected_types = expected if isinstance(expected, list) else [expected]
    if expected and not any(_matches_type(value, item) for item in expected_types):
        raise ValueError(f"{path}: expected {expected}, got {type(value).__name__}.")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: value is outside the allowed options.")
    if isinstance(value, dict):
        missing = set(schema.get("required", [])) - value.keys()
        if missing:
            raise ValueError(f"{path}: missing required field(s): {', '.join(sorted(missing))}.")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = value.keys() - properties.keys()
            if extras:
                raise ValueError(f"{path}: unexpected field(s): {', '.join(sorted(extras))}.")
        for key, child in properties.items():
            if key in value:
                _validate_schema(value[key], child, f"{path}.{key}")
    elif isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, child in enumerate(value):
            _validate_schema(child, schema["items"], f"{path}[{index}]")


def _matches_type(value: Any, expected: str | None) -> bool:
    return {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }.get(expected, lambda _item: True)(value)
