"""Local API for the LeetCode code assistant extension.

Run with: venv/bin/python final-version/server.py
The server only listens on 127.0.0.1.
"""

from __future__ import annotations

import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agents.analysis_supervisor import build_analysis_graph
from agents.error_repair_agent import build_repair_graph
from utils.execution_manager import ExecutionManager, check_snippet_syntax
from utils.leetcode_runtime_harness import build_runtime_driver

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8765
MAX_REQUEST_BYTES = 200_000
MAX_SOURCE_CHARS = 100_000
ALLOWED_HOSTS = {"127.0.0.1:8765", "localhost:8765"}
YOUTUBE_SEARCH_FILE = ROOT / "utils" / "yt-search.py"


repair_graph = build_repair_graph()
analysis_graph = build_analysis_graph()


def _analyze_program_uncached(
    code: str, language: str, provider_settings: dict[str, Any] | None
) -> dict[str, Any]:
    result = analysis_graph.invoke(
        {"code": code, "language": language, "provider_settings": provider_settings}
    )
    return {
        "approach_analysis": result["approach_analysis"],
        "complexity_analysis": result["complexity_analysis"],
        "optimization_analysis": result["optimization_analysis"],
        "final_report": result["final_report"],
    }


def analyze_program(
    code: str,
    language: str,
    provider_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not code.strip():
        raise ValueError("Enter some code before requesting analysis.")
    if language not in {"Python", "C++"}:
        raise ValueError("Choose Python or C++.")
    if len(code) > MAX_SOURCE_CHARS:
        raise ValueError("Code is too large. The limit is 100,000 characters.")

    return _analyze_program_uncached(code, language, provider_settings)


def _invoke_repair_agent(
    code: str,
    language: str,
    failure: dict[str, Any],
    provider_settings: dict[str, Any] | None,
) -> dict[str, Any]:
    result = repair_graph.invoke({
        "code": code,
        "language": language,
        "execution": failure,
        "provider_settings": provider_settings,
    })
    return result


def _run_with_driver(code: str, language: str, driver: str) -> dict[str, Any]:
    complete_program = code.rstrip() + "\n\n" + driver.strip() + "\n"
    # Confirm the combined snippet and generated driver compile before executing.
    syntax = check_snippet_syntax(complete_program, language)
    if syntax["status"] != "success":
        return {"status": "harness_error", "stdout": syntax.get("stdout", ""),
                "stderr": syntax.get("stderr", "")}
    with ExecutionManager(complete_program, language) as runner:
        prepared = runner.prepare()
        if prepared.get("status") != "success":
            # The source snippet already passed its standalone syntax check; a
            # failure here is likely a mismatch in the generated driver.
            return {"status": "harness_error", "stdout": prepared.get("stdout", ""),
                    "stderr": prepared.get("stderr", "")}
        return runner.execute("")


def repair_code(
    code: str,
    language: str,
    problem_context: dict[str, Any] | None = None,
    provider_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not code.strip():
        raise ValueError("Enter some code before asking the repair agent.")
    if language not in {"Python", "C++"}:
        raise ValueError("Choose Python or C++.")
    if len(code) > MAX_SOURCE_CHARS:
        raise ValueError("Code is too large. The limit is 100,000 characters.")

    # Run a deterministic syntax/compile check first. The repair LLM is never
    # used to decide whether an error exists.
    failure = check_snippet_syntax(code, language)
    failure_phase = failure.get("status")
    verification = ""
    if failure_phase in {"syntax_error", "compilation_error"}:
        failure = {**failure, "phase": failure_phase}
        verification = "The proposed correction was checked again with the local syntax/compiler check."
    elif failure_phase in {"unavailable", "check_timeout"}:
        return {
            "error_type": "none", "diagnosis": failure.get("stderr") or "The local compiler check was unavailable.",
            "has_fix": False, "corrected_code": "",
            "verification_message": "No repair was suggested because the local syntax/compile check could not complete.",
        }
    else:
        examples = problem_context.get("examples", []) if isinstance(problem_context, dict) else []
        examples = examples if isinstance(examples, list) else []
        driver_result = build_runtime_driver(code, language, examples, provider_settings)
        if not driver_result["can_run"]:
            return {
                "error_type": "none",
                "diagnosis": "The snippet passed the local syntax/compile check. Runtime checking was skipped because a reliable driver could not be built.",
                "has_fix": False, "corrected_code": "",
                "verification_message": driver_result["reason"],
            }
        run_result = _run_with_driver(code, language, driver_result["driver"])
        if run_result["status"] == "harness_error":
            return {
                "error_type": "none",
                "diagnosis": "The snippet passed its local syntax/compile check, but the generated example driver could not be compiled reliably.",
                "has_fix": False, "corrected_code": "",
                "verification_message": (run_result.get("stderr") or "The runtime harness could not be compiled.")[:4000],
            }
        if run_result["status"] == "success":
            return {
                "error_type": "none",
                "diagnosis": "No syntax, compilation, or runtime error was found in the captured example run.",
                "has_fix": False, "corrected_code": "",
                "verification_message": "The snippet compiled and completed the captured example calls without a runtime error.",
            }
        failure = {**run_result, "phase": "runtime", "failing_input": "Captured LeetCode example calls"}
        failure_phase = "runtime"
        verification = "The proposed correction was checked again against the captured LeetCode examples."

    result = _invoke_repair_agent(code, language, failure, provider_settings)
    corrected = result.get("corrected_code", "") if result.get("has_fix") else ""
    if corrected:
        corrected_syntax = check_snippet_syntax(corrected, language)
        if corrected_syntax.get("status") != "success":
            return {
                "error_type": result.get("error_type", "none"),
                "diagnosis": result.get("diagnosis", "A possible fix was proposed, but it did not pass the local compiler check."),
                "has_fix": False, "corrected_code": "",
                "verification_message": "The proposed correction still has a syntax/compilation error, so it was not shown as a verified fix.\n" + str(corrected_syntax.get("stderr", ""))[:3000],
            }
        if failure_phase == "runtime":
            verified_run = _run_with_driver(corrected, language, driver_result["driver"])
            if verified_run.get("status") != "success":
                return {
                    "error_type": result.get("error_type", "runtime"),
                    "diagnosis": result.get("diagnosis", "The suggested change did not clear the observed runtime failure."),
                    "has_fix": False, "corrected_code": "",
                    "verification_message": "The suggested correction did not pass the captured example runtime check.\n" + str(verified_run.get("stderr", ""))[:3000],
                }
    actual_error_type = {
        "syntax_error": "syntax",
        "compilation_error": "compilation",
        "runtime_error": "runtime",
        "time_limit": "runtime",
    }.get(failure_phase, result.get("error_type", "none"))
    agent_diagnosis = str(result.get("diagnosis", "")).strip()
    diagnosis = f"Local {actual_error_type} check failed."
    if agent_diagnosis:
        diagnosis += " " + agent_diagnosis
    return {
        "error_type": actual_error_type,
        "diagnosis": diagnosis,
        "has_fix": bool(corrected),
        "corrected_code": corrected,
        "verification_message": verification + (" The correction passed the re-check." if corrected else " No verified correction was returned."),
        "diagnostics": str(failure.get("stderr", ""))[:4000],
    }


def search_youtube_videos(question_name: str, language: str) -> list[dict[str, Any]]:
    if not question_name.strip():
        raise ValueError("Capture a question or enter a question name first.")
    if language not in {"Python", "C++"}:
        raise ValueError("Choose Python or C++ before searching YouTube.")
    spec = importlib.util.spec_from_file_location("leetcode_youtube_search", YOUTUBE_SEARCH_FILE)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the YouTube search utility.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.search_youtube(question_name[:300], language)


class AppHandler(BaseHTTPRequestHandler):
    server_version = "LocalCodeRunner/1.0"

    def _request_allowed(self) -> bool:
        origin = self.headers.get("Origin", "")
        host = self.headers.get("Host", "").lower()
        return origin.startswith("chrome-extension://") and host in ALLOWED_HOSTS

    def do_GET(self) -> None:
        self.send_error(404)

    def do_POST(self) -> None:
        if not self._request_allowed():
            self._send_json(403, {"message": "Forbidden"})
            return
        if self.path not in {"/api/repair", "/api/analyze", "/api/youtube-search"}:
            self.send_error(404)
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
                raise ValueError("Request is empty or too large.")
            payload = json.loads(self.rfile.read(content_length))
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object.")
            code = str(payload.get("code", ""))
            language = str(payload.get("language", ""))
            provider_settings = payload.get("provider_settings")
            if provider_settings is not None and not isinstance(provider_settings, dict):
                raise ValueError("Provider settings must be an object.")
            if self.path == "/api/repair":
                context = payload.get("problem_context")
                result = repair_code(
                    code, language, context if isinstance(context, dict) else None,
                    provider_settings,
                )
            elif self.path == "/api/analyze":
                result = analyze_program(code, language, provider_settings)
            elif self.path == "/api/youtube-search":
                result = {
                    "videos": search_youtube_videos(
                        str(payload.get("question_name", "")),
                        str(payload.get("language", "")),
                    )
                }
            self._send_json(200, result)
        except (ValueError, json.JSONDecodeError) as error:
            self._send_json(400, {"status": "request_error", "message": str(error)})
        except Exception as error:
            self._send_json(500, {"status": "server_error", "message": str(error)})

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        origin = self.headers.get("Origin", "")
        if origin.startswith("chrome-extension://"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"Local Code Runner ready at http://{HOST}:{PORT}")
    print("Stop it with Ctrl+C.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
