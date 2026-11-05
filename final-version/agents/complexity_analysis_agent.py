"""Structured time and space complexity estimation agent."""

from __future__ import annotations

from typing import Any

from .llm import structured

COMPLEXITY_SCHEMA = {
    "type": "object",
    "properties": {
        "time_complexity": {"type": "string"},
        "space_complexity": {"type": "string"},
    },
    "required": ["time_complexity", "space_complexity"],
    "additionalProperties": False,
}

INSTRUCTIONS = """Estimate worst-case time and auxiliary space Big O for this Python/C++
code. Use the parsed structure to ground loop and recursion estimates. Do not
guess constraints or claim constant space if storage grows with input. Return
only time_complexity and space_complexity as concise Big O values."""


def analyze_complexity(
    code: str,
    language: str,
    approach_analysis: dict[str, Any],
    parsed_code: dict[str, Any],
    provider_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if language not in {"Python", "C++"}:
        raise ValueError("Language must be either Python or C++.")
    result = structured(
        "complexity_analysis",
        COMPLEXITY_SCHEMA,
        INSTRUCTIONS,
        {
            "language": language,
            "source_code": code,
            "approach_analysis": approach_analysis,
            "parsed_structure": parsed_code,
        },
        provider_settings,
    )
    return result
