"""Local syntax checks and bounded execution for Python and C++ snippets."""

from __future__ import annotations

import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

MAX_OUTPUT_BYTES = 1_000_000

CPP_PRELUDE = """#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <deque>
#include <functional>
#include <iostream>
#include <limits>
#include <list>
#include <map>
#include <numeric>
#include <queue>
#include <set>
#include <sstream>
#include <stack>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>
using namespace std;
"""


def _ignore_cpp_includes(code: str) -> str:
    """LeetCode supplies headers; omit snippet include directives during checks."""
    return re.sub(r"(?m)^\s*#\s*include\b[^\n]*(?:\n|$)", "\n", code)


def _cpp_prelude(code: str) -> str:
    """Supply common judge types only when the submitted snippet lacks them."""
    declarations = {
        "ListNode": "struct ListNode { int val; ListNode *next; ListNode(int x=0, ListNode *n=nullptr): val(x), next(n) {} };\n",
        "TreeNode": "struct TreeNode { int val; TreeNode *left; TreeNode *right; TreeNode(int x=0, TreeNode *l=nullptr, TreeNode *r=nullptr): val(x), left(l), right(r) {} };\n",
        "Node": "struct Node { int val; vector<Node*> neighbors; Node(): val(0), neighbors() {} Node(int x): val(x), neighbors() {} Node(int x, vector<Node*> n): val(x), neighbors(n) {} };\n",
    }
    supplied = "".join(
        declaration for name, declaration in declarations.items()
        if not re.search(r"\b(?:struct|class)\s+" + name + r"\b", code)
        and re.search(r"\b" + name + r"\b", code)
    )
    return CPP_PRELUDE + supplied


def check_snippet_syntax(code: str, language: str) -> dict[str, Any]:
    """Compile a LeetCode snippet without requiring it to contain a main()."""
    if language == "Python":
        try:
            compile(code, "submitted_code.py", "exec")
            return {"status": "success", "stdout": "", "stderr": ""}
        except (SyntaxError, ValueError, TypeError) as error:
            line = getattr(error, "lineno", None)
            location = f" at line {line}" if line else ""
            return {"status": "syntax_error", "stdout": "", "stderr": f"{error.__class__.__name__}{location}: {error}"}

    if language != "C++":
        raise ValueError("Language must be either Python or C++.")
    compiler = _compiler()
    if not compiler:
        return {"status": "unavailable", "stdout": "", "stderr": "No C++ compiler found. Install c++, g++, or clang++."}

    with tempfile.TemporaryDirectory(prefix="snippet-check-") as temp_dir:
        source = Path(temp_dir) / "snippet.cpp"
        source.write_text(
            _cpp_prelude(code) + "\n#line 1 \"submitted_code.cpp\"\n" + _ignore_cpp_includes(code),
            encoding="utf-8",
        )
        try:
            result = subprocess.run(
                [compiler, "-std=c++17", "-fsyntax-only", str(source)],
                cwd=temp_dir, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=8, check=False,
            )
        except subprocess.TimeoutExpired as error:
            return {"status": "check_timeout", "stdout": _decode(error.stdout),
                    "stderr": _decode(error.stderr) or "C++ syntax check timed out."}
        return {
            "status": "success" if result.returncode == 0 else "compilation_error",
            "stdout": _decode(result.stdout),
            "stderr": _decode(result.stderr),
            "returncode": result.returncode,
        }


class ExecutionManager:
    """Compile one complete driver program, then run it on multiple inputs."""

    def __init__(
        self,
        code: str,
        language: str,
        timeout: float = 3.0,
        memory_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        if language not in {"Python", "C++"}:
            raise ValueError("Language must be either Python or C++.")
        if not code.strip():
            raise ValueError("Program cannot be empty.")
        self.code = code
        self.language = language
        self.timeout = timeout
        self.memory_bytes = memory_bytes
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="leetcode-check-")
        self.workdir = Path(self._temporary_directory.name)
        self.launcher = self.workdir / "limited_exec.py"
        self.prepared = False
        self.command: list[str] = []
        self.compilation_time = 0.0
        self._write_launcher()

    def _write_launcher(self) -> None:
        cpu_limit = max(1, math.ceil(self.timeout))
        launcher_code = f"""import os, sys
try:
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, ({cpu_limit}, {cpu_limit + 1}))
    resource.setrlimit(resource.RLIMIT_AS, ({self.memory_bytes}, {self.memory_bytes}))
    resource.setrlimit(resource.RLIMIT_FSIZE, ({MAX_OUTPUT_BYTES}, {MAX_OUTPUT_BYTES}))
except (ImportError, AttributeError, OSError, ValueError):
    pass
target = sys.argv[1]
os.execv(target, [target, *sys.argv[2:]])
"""
        self.launcher.write_text(launcher_code, encoding="utf-8")

    def prepare(self) -> dict[str, Any]:
        if self.prepared:
            return {"status": "success", "compilation_time": self.compilation_time}
        extension = ".py" if self.language == "Python" else ".cpp"
        source = self.workdir / f"program{extension}"
        source.write_text(self.code, encoding="utf-8")
        if self.language == "Python":
            try:
                compile(self.code, str(source), "exec")
            except (SyntaxError, ValueError, TypeError) as error:
                return {"status": "syntax_error", "compilation_time": 0.0,
                        "stdout": "", "stderr": str(error), "returncode": None}
            self.command = [sys.executable, "-I", str(source)]
            self.prepared = True
            return {"status": "success", "compilation_time": 0.0}

        compiler = _compiler()
        if not compiler:
            return {"status": "compilation_error", "compilation_time": 0.0,
                    "stdout": "", "stderr": "No C++ compiler found.", "returncode": None}
        executable = self.workdir / "program"
        source.write_text(
            _cpp_prelude(self.code) + "\n#line 1 \"submitted_code.cpp\"\n" + _ignore_cpp_includes(self.code),
            encoding="utf-8",
        )
        started = time.perf_counter()
        try:
            result = subprocess.run(
                [compiler, "-std=c++17", "-O2", str(source), "-o", str(executable)],
                cwd=self.workdir, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=self.timeout, check=False,
            )
        except subprocess.TimeoutExpired as error:
            self.compilation_time = time.perf_counter() - started
            return {"status": "compilation_timeout", "compilation_time": self.compilation_time,
                    "stdout": _decode(error.stdout),
                    "stderr": _decode(error.stderr) or "C++ compilation timed out.",
                    "returncode": None}
        self.compilation_time = time.perf_counter() - started
        if result.returncode:
            return {"status": "compilation_error", "compilation_time": self.compilation_time,
                    "stdout": _decode(result.stdout), "stderr": _decode(result.stderr),
                    "returncode": result.returncode}
        self.command = [str(executable)]
        self.prepared = True
        return {"status": "success", "compilation_time": self.compilation_time}

    def execute(self, stdin: str = "") -> dict[str, Any]:
        if not self.prepared:
            raise RuntimeError("Call prepare() before execute().")
        stdout_path, stderr_path = self.workdir / "stdout", self.workdir / "stderr"
        started = time.perf_counter()
        process: subprocess.Popen[bytes] | None = None
        timed_out = False
        try:
            with stdout_path.open("wb") as out_file, stderr_path.open("wb") as err_file:
                process = subprocess.Popen(
                    [sys.executable, "-I", str(self.launcher), *self.command],
                    cwd=self.workdir, stdin=subprocess.PIPE,
                    stdout=out_file, stderr=err_file,
                    start_new_session=(os.name == "posix"),
                )
                try:
                    process.communicate(stdin.encode("utf-8"), timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self._kill_process(process)
                    process.communicate()
        except OSError as error:
            return {"status": "runtime_error", "stdout": "", "stderr": str(error),
                    "returncode": None, "execution_time": time.perf_counter() - started}
        elapsed = time.perf_counter() - started
        stdout, stderr = _read_limited(stdout_path), _read_limited(stderr_path)
        cpu_timeout = process is not None and process.returncode == -getattr(signal, "SIGXCPU", 24)
        status = "time_limit" if timed_out or cpu_timeout else (
            "success" if process is not None and process.returncode == 0 else "runtime_error"
        )
        return {"status": status, "stdout": stdout, "stderr": stderr,
                "returncode": process.returncode if process else None,
                "execution_time": elapsed}

    @staticmethod
    def _kill_process(process: subprocess.Popen[bytes]) -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:  # pragma: no cover - Windows
                process.kill()
        except ProcessLookupError:
            pass

    def cleanup(self) -> None:
        self._temporary_directory.cleanup()
        self.prepared = False

    def __enter__(self) -> "ExecutionManager":
        return self

    def __exit__(self, *_: object) -> None:
        self.cleanup()


def _compiler() -> str | None:
    return os.environ.get("CXX") or shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")


def _read_limited(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            return _decode(stream.read(MAX_OUTPUT_BYTES))
    except FileNotFoundError:
        return ""


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:MAX_OUTPUT_BYTES]
    return value[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
