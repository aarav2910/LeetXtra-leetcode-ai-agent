"""LangGraph supervisor coordinating code analysis and reporting agents."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .approach_agent import analyze_approach
from .complexity_analysis_agent import analyze_complexity
from .optimization_agent import suggest_optimizations
from .explanation_report_agent import create_report
from utils.code_parser import parse_code, summarize_parse_for_agents


class AnalysisState(TypedDict, total=False):
    code: str
    language: str
    provider_settings: dict[str, Any]
    parsed_code: dict[str, Any]
    static_facts: dict[str, Any]
    approach_analysis: dict[str, Any]
    complexity_analysis: dict[str, Any]
    optimization_analysis: dict[str, Any]
    final_report: dict[str, Any]
    next_agent: str


def build_analysis_graph():
    def supervisor(state: AnalysisState) -> dict[str, str]:
        if "parsed_code" not in state:
            return {"next_agent": "code_parser"}
        if "complexity_analysis" not in state:
            return {"next_agent": "complexity_analysis"}
        if "optimization_analysis" not in state:
            return {"next_agent": "optimization"}
        return {"next_agent": "report"}

    def route(state: AnalysisState) -> str:
        return state["next_agent"]

    def run_code_parser(state: AnalysisState) -> dict[str, Any]:
        parsed = parse_code(state["code"], state["language"])
        return {
            "parsed_code": parsed,
            "static_facts": summarize_parse_for_agents(parsed),
        }

    def run_approach_analysis(state: AnalysisState) -> dict[str, Any]:
        return {
            "approach_analysis": analyze_approach(
                state["code"], state["language"], state["static_facts"],
                state.get("provider_settings"),
            )
        }

    def run_complexity_analysis(state: AnalysisState) -> dict[str, Any]:
        return {
            "complexity_analysis": analyze_complexity(
                state["code"],
                state["language"],
                state["approach_analysis"],
                state["static_facts"],
                state.get("provider_settings"),
            )
        }

    def run_optimization(state: AnalysisState) -> dict[str, Any]:
        return {
            "optimization_analysis": suggest_optimizations(
                state["code"],
                state["language"],
                state["approach_analysis"],
                state["complexity_analysis"],
                state["static_facts"],
                state.get("provider_settings"),
            )
        }

    def run_report(state: AnalysisState) -> dict[str, Any]:
        return {
            "final_report": create_report(
                state["approach_analysis"],
                state["complexity_analysis"],
                state["optimization_analysis"],
            )
        }

    workflow = StateGraph(AnalysisState)
    workflow.add_node("supervisor", supervisor)
    workflow.add_node("code_parser", run_code_parser)
    workflow.add_node("approach_analysis", run_approach_analysis)
    workflow.add_node("complexity_analysis", run_complexity_analysis)
    workflow.add_node("optimization", run_optimization)
    workflow.add_node("report", run_report)
    workflow.add_edge(START, "supervisor")
    workflow.add_conditional_edges(
        "supervisor",
        route,
        {
            "code_parser": "code_parser",
            "complexity_analysis": "complexity_analysis",
            "optimization": "optimization",
            "report": "report",
        },
    )
    workflow.add_edge("code_parser", "approach_analysis")
    workflow.add_edge("approach_analysis", "supervisor")
    workflow.add_edge("complexity_analysis", "supervisor")
    workflow.add_edge("optimization", "supervisor")
    workflow.add_edge("report", END)
    return workflow.compile()
