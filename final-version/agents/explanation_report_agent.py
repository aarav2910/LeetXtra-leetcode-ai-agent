"""Build a compact final report directly from the other agent results."""

from __future__ import annotations

from typing import Any


def create_report(
    approach_analysis: dict[str, Any],
    complexity_analysis: dict[str, Any],
    optimization_analysis: dict[str, Any],
) -> dict[str, Any]:
    current_approach = approach_analysis.get(
        "current_approach", "No specific named approach is clear."
    )
    key_idea = approach_analysis.get("key_idea", "No key idea was identified.")

    options = optimization_analysis.get("approaches", [])
    if options:
        selected = options[0]
        suggested_approach = selected.get("technique") or selected.get("name") or current_approach
        optimization_summary = (
            f"{selected.get('name', 'An approach')} ({selected.get('technique', 'algorithmic improvement')}): "
            f"{selected.get('time_complexity', 'time complexity not stated')}; "
            f"{selected.get('space_complexity', 'space complexity not stated')}. "
            f"{selected.get('why_it_helps', '')}"
        ).strip()
        recommendation = (
            f"Review and test {selected.get('name', 'the proposed approach')} before using it."
        )
    else:
        suggested_approach = current_approach
        optimization_summary = optimization_analysis.get(
            "overall_note", "No large Big O improvement was identified."
        )
        recommendation = "Keep the current approach; no large Big O improvement was identified from the available information."

    return {
        "overview": f"Current approach: {current_approach}. Key idea: {key_idea}",
        "approach_card": {
            "current": current_approach,
            "suggested": suggested_approach,
            "key_idea": key_idea,
        },
        "complexity_takeaway": (
            f"Time: {complexity_analysis.get('time_complexity', 'not estimated')}. "
            f"Space: {complexity_analysis.get('space_complexity', 'not estimated')}."
        ),
        "optimization_result": optimization_summary,
        "recommendation": recommendation,
    }
