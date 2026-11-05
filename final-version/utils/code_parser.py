"""Extract deterministic source locations and structure with Tree-sitter."""

from __future__ import annotations

from typing import Any


PYTHON_NODE_TYPES = {
    "function": {"function_definition"},
    "loop": {"for_statement", "while_statement"},
    "call": {"call"},
}

CPP_NODE_TYPES = {
    "function": {"function_definition"},
    "loop": {"for_statement", "while_statement", "do_statement", "for_range_loop"},
    "call": {"call_expression"},
}

LOOP_TYPES = {
    "for_statement": "for loop",
    "while_statement": "while loop",
    "do_statement": "do-while loop",
    "for_range_loop": "range-based for loop",
}


def _node_text(node: Any, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _walk(node: Any):
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(reversed(current.children))


def _function_name(node: Any, source: bytes, language: str) -> str:
    if language == "Python":
        name_node = node.child_by_field_name("name")
        return _node_text(name_node, source) if name_node else "<unknown>"

    declarator = node.child_by_field_name("declarator")
    while declarator:
        if declarator.type == "function_declarator":
            inner = declarator.child_by_field_name("declarator")
            if inner is None:
                break
            declarator = inner
            continue
        if declarator.type == "qualified_identifier":
            name = declarator.child_by_field_name("name")
            return _node_text(name, source) if name else _node_text(declarator, source)
        if declarator.type in {"identifier", "field_identifier", "operator_name"}:
            return _node_text(declarator, source)

        next_declarator = declarator.child_by_field_name("declarator")
        if next_declarator is not None:
            declarator = next_declarator
            continue
        nested = next(
            (child for child in declarator.named_children if child.type != "parameter_list"),
            None,
        )
        declarator = nested

    return "<unknown>"


def _call_name(node: Any, source: bytes) -> str:
    function = node.child_by_field_name("function")
    if function is None:
        return "<unknown>"

    # Keep a readable callee name (e.g. `obj.method`) without arguments.
    return _node_text(function, source).strip()[:120]


def _unavailable(language: str, message: str) -> dict[str, Any]:
    return {
        "language": language,
        "status": "unavailable",
        "message": message,
        "has_syntax_errors": None,
        "syntax_errors": [],
        "functions": [],
        "loops": [],
        "nested_loops": [],
        "function_calls": [],
    }


def summarize_parse_for_agents(parsed: dict[str, Any]) -> dict[str, Any]:
    """Keep the LLM context small while retaining useful exact source facts."""
    if parsed.get("status") != "parsed":
        return parsed
    functions = parsed.get("functions", [])
    loops = parsed.get("loops", [])
    nested_loops = parsed.get("nested_loops", [])
    calls = parsed.get("function_calls", [])
    counts = parsed.get("counts", {})
    return {
        "language": parsed.get("language"),
        "status": parsed.get("status"),
        "has_syntax_errors": parsed.get("has_syntax_errors"),
        "syntax_errors": parsed.get("syntax_errors", [])[:20],
        "counts": {
            "functions": counts.get("functions", len(functions)),
            "loops": counts.get("loops", len(loops)),
            "nested_loops": counts.get("nested_loops", len(nested_loops)),
            "function_calls": counts.get("function_calls", len(calls)),
        },
        "functions": functions[:60],
        "loops": loops[:100],
        "nested_loops": nested_loops[:60],
        "function_calls": calls[:80],
    }


def parse_code(code: str, language: str) -> dict[str, Any]:
    """Return syntax and structural facts; never infer algorithm complexity."""
    if language not in {"Python", "C++"}:
        raise ValueError("Language must be either Python or C++.")

    try:
        from tree_sitter import Language as TreeSitterLanguage
        from tree_sitter import Parser
        if language == "Python":
            import tree_sitter_python as grammar
        else:
            import tree_sitter_cpp as grammar
    except ImportError:
        return _unavailable(
            language,
            "Tree-sitter packages are missing. Install the project requirements to enable deterministic parsing.",
        )

    source = code.encode("utf-8")
    try:
        parser = Parser(TreeSitterLanguage(grammar.language()))
        tree = parser.parse(source)
    except (TypeError, ValueError, RuntimeError):
        return _unavailable(language, "The installed Tree-sitter grammar is incompatible with the parser version.")
    if tree is None:
        return _unavailable(language, "Tree-sitter could not parse the source.")

    node_types = PYTHON_NODE_TYPES if language == "Python" else CPP_NODE_TYPES
    nodes = list(_walk(tree.root_node))
    functions = []
    for node in nodes:
        if node.type in node_types["function"]:
            functions.append({
                "name": _function_name(node, source, language),
                "start_line": node.start_point.row + 1,
                "end_line": node.end_point.row + 1,
            })

    loop_nodes = [node for node in nodes if node.type in node_types["loop"]]
    loops = []
    nested_loops = []
    for node in loop_nodes:
        ancestor = node.parent
        enclosing_loops = []
        while ancestor is not None:
            if ancestor.type in node_types["loop"]:
                enclosing_loops.append(ancestor)
            ancestor = ancestor.parent

        start_line = node.start_point.row + 1
        loop_info = {
            "type": LOOP_TYPES.get(node.type, node.type),
            "start_line": start_line,
            "end_line": node.end_point.row + 1,
            "nesting_depth": len(enclosing_loops),
        }
        loops.append(loop_info)
        if enclosing_loops:
            outermost = enclosing_loops[-1]
            nested_loops.append({
                "outer_type": LOOP_TYPES.get(outermost.type, outermost.type),
                "outer_start_line": outermost.start_point.row + 1,
                "inner_type": LOOP_TYPES.get(node.type, node.type),
                "inner_start_line": start_line,
                "depth": len(enclosing_loops),
            })

    calls = [
        {
            "name": _call_name(node, source),
            "line": node.start_point.row + 1,
        }
        for node in nodes
        if node.type in node_types["call"]
    ]

    syntax_errors = []
    for node in nodes:
        if node.type == "ERROR" or node.is_missing:
            syntax_errors.append({
                "type": "missing" if node.is_missing else "parse_error",
                "line": node.start_point.row + 1,
                "end_line": node.end_point.row + 1,
                "text": _node_text(node, source)[:160],
            })
            if len(syntax_errors) >= 100:
                break

    return {
        "language": language,
        "status": "parsed",
        "message": "Tree-sitter found syntax errors; it may still provide partial structure." if syntax_errors else "",
        "has_syntax_errors": bool(syntax_errors),
        "syntax_errors": syntax_errors,
        "counts": {
            "functions": len(functions),
            "loops": len(loops),
            "nested_loops": len(nested_loops),
            "function_calls": len(calls),
        },
        "functions": functions[:300],
        "loops": loops[:500],
        "nested_loops": nested_loops[:500],
        "function_calls": calls[:500],
    }
