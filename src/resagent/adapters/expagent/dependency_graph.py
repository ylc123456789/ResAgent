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
    if len(identifiers) != len(actions):
        issues.append("every recommended action must have a non-empty action_id")
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

    # Requirement flows backward along hard dependencies: every dependency of
    # a REQUIRED action must itself be required. A required action depending
    # on an optional one is a scheduler trap — the "optional" dependency is
    # forced to execute (or the required dependent can never run).
    required_by_id = {
        str(action.get("action_id", "")).strip(): bool(action.get("required", True))
        for action in actions
        if str(action.get("action_id", "")).strip()
    }
    for action in actions:
        action_id = str(action.get("action_id", "")).strip()
        if not bool(action.get("required", True)):
            continue
        for dependency in (str(value).strip() for value in action.get("depends_on", [])):
            if dependency in required_by_id and not required_by_id[dependency]:
                issues.append(
                    f"required action '{action_id}' depends on optional action "
                    f"'{dependency}' — a required action's dependencies must all "
                    f"be required (require the whole chain, or mark this action "
                    f"optional)"
                )
    return issues
