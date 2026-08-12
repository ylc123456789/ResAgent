"""Validation for ExpAgent recommendation dependency graphs."""

from __future__ import annotations


def dependency_graph_issues(actions: list[dict]) -> list[str]:
    """Validate decision-local dependency IDs and reject cycles atomically."""
    issues: list[str] = []
    identifiers = [
        str(action.get("action_id", "")).strip()
        for action in actions
        if str(action.get("action_id", "")).strip()
    ]
    if len(identifiers) != len(set(identifiers)):
        issues.append("recommended actions contain duplicate action_id values")

    known = set(identifiers)
    graph: dict[str, list[str]] = {}
    for index, action in enumerate(actions):
        action_id = str(action.get("action_id", "")).strip()
        dependencies = [str(value).strip() for value in action.get("depends_on", [])]
        for dependency in dependencies:
            if dependency not in known:
                issues.append(
                    f"recommended action {action_id or index} has unknown dependency {dependency!r}"
                )
        if action_id:
            graph[action_id] = dependencies

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        cyclic = any(visit(dep) for dep in graph.get(node, []) if dep in graph)
        visiting.remove(node)
        visited.add(node)
        return cyclic

    if any(visit(node) for node in graph if node not in visited):
        issues.append("recommended action dependency graph contains a cycle")
    return issues
