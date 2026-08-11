"""LLM-based next-action planner for the agentic loop.

Given the current ResearchState, asks the LLM to pick the next orchestration
action. Returns a typed action that the controller can execute.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .models import ResearchState, ActionName
from .prompts import CONTROLLER_SYSTEM
from .context import build_controller_context


@dataclass
class PlannedAction:
    """A single action chosen by the LLM planner."""
    action: ActionName
    params: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    analysis: str = ""


class Planner:
    """Calls an LLM to decide the next orchestration action."""

    def __init__(
        self,
        api_base: str = "https://api.deepseek.com",
        api_key_env: str = "DEEPSEEK_API_KEY",
        model: str = "deepseek-v4-pro",
        mock: bool = False,
    ):
        self.api_base = api_base
        self.api_key_env = api_key_env
        self.model = model
        self.mock = mock

    def choose_action(self, state: ResearchState) -> PlannedAction:
        """Observe state and return the next planned action."""
        context = build_controller_context(state, model=self.model)

        if self.mock:
            return self._mock_choose(state, context)

        raw = self._call_llm(context)
        return self._parse_response(raw)

    def classify_failure(self, task_id: str, error_message: str) -> dict:
        """Classify a task failure. Returns category dict."""
        from .prompts import FAILURE_CLASSIFIER

        prompt = (
            f"Task: {task_id}\n"
            f"Error:\n{error_message[:2000]}\n\n"
            f"Classify this failure."
        )
        if self.mock:
            return {
                "category": "unknown", "confidence": "low",
                "explanation": "mock classifier",
                "recommended_action": "investigate",
            }

        raw = self._call_llm_raw(FAILURE_CLASSIFIER, prompt)
        try:
            return _extract_json(raw)
        except Exception:
            return {
                "category": "unknown", "confidence": "low",
                "explanation": "could not parse LLM response",
                "recommended_action": "investigate",
            }

    # -- internal -----------------------------------------------------------

    def _call_llm(self, context: str) -> str:
        return self._call_llm_raw(CONTROLLER_SYSTEM, context)

    def _call_llm_raw(self, system: str, user: str) -> str:
        import httpx

        api_key = os.environ.get(self.api_key_env, "")
        if not api_key:
            raise RuntimeError(
                f"API key not found. Set {self.api_key_env} env var."
            )

        client = httpx.Client(
            timeout=60,
            trust_env=False,  # bypass proxy env vars that cause SSL issues
        )
        resp = client.post(
            f"{self.api_base}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
                "max_tokens": 4000,
            },
        )
        resp.raise_for_status()
        body = resp.json()
        return body["choices"][0]["message"]["content"]

    def _parse_response(self, raw: str) -> PlannedAction:
        """Parse LLM JSON response into a PlannedAction."""
        data = _extract_json(raw)

        action_str = data.get("action", "finish")
        try:
            action = ActionName(action_str)
        except ValueError:
            action = ActionName.finish

        return PlannedAction(
            action=action,
            params=data.get("params", {}),
            reason=data.get("reason", ""),
            analysis=data.get("analysis", ""),
        )

    def _mock_choose(self, state: ResearchState, context: str) -> PlannedAction:
        """Deterministic mock for testing without LLM.

        Logic: call_exp_agent if no artifacts yet.
        Otherwise execute the first pending task.
        Otherwise finish.
        """
        if not state.artifacts:
            return PlannedAction(
                action=ActionName.call_exp_agent,
                params={
                    "reason": "Initial consultation",
                    "focus": "Analyze research goal",
                },
                reason="No artifacts yet -- need ExpAgent to analyze the goal.",
                analysis=(
                    "Starting research. First step is always scientific "
                    "consultation."
                ),
            )

        pending = [t for t in state.tasks if t.status.value == "pending"]
        if pending:
            t = pending[0]
            agent_to_action = {
                "ExpAgent": ActionName.call_exp_agent,
                "CodingAgent": ActionName.call_coding_agent,
                "ReproAgent": ActionName.call_repro_agent,
            }
            action = agent_to_action.get(t.agent.value, ActionName.finish)
            return PlannedAction(
                action=action,
                params={"task_id": t.id, **t.input},
                reason=f"Executing pending task {t.id}.",
                analysis=(
                    f"Task {t.id} is the highest-priority pending task."
                ),
            )

        return PlannedAction(
            action=ActionName.finish,
            params={
                "summary": "All tasks completed.",
                "reason": "No pending tasks.",
            },
            reason="No pending tasks remaining.",
            analysis="Research run has completed all tasks.",
        )


def _extract_json(raw: str) -> dict:
    """Extract JSON object from LLM response, handling markdown fences
    and gracefully repairing truncated JSON."""
    text = raw.strip()

    # Remove markdown fences
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:]) if len(lines) > 1 else text
        if text.endswith("```"):
            text = text[:-3]

    # Find JSON object boundaries
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        text = text[start:end]

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Repair: close unterminated strings and structures
    repaired = _repair_truncated_json(text)
    return json.loads(repaired)


def _repair_truncated_json(text: str) -> str:
    """Attempt to repair a truncated JSON object by closing open structures."""
    # Close any unterminated string, then close open braces/brackets so the
    # structure balances. Does NOT truncate to a last valid key-value pair;
    # a trailing comma is stripped afterward so the result is valid JSON.

    # First, try to close any unclosed string
    in_string = False
    escape = False
    cleaned = []
    for ch in text:
        if escape:
            escape = False
            cleaned.append(ch)
            continue
        if ch == "\\":
            escape = True
            cleaned.append(ch)
            continue
        if ch == '"' and not escape:
            in_string = not in_string
        cleaned.append(ch)

    # If still in string, close it
    if in_string:
        cleaned.append('"')

    text = "".join(cleaned)

    # Count and close unclosed braces/brackets
    braces = 0
    brackets = 0
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            braces += 1
        elif ch == "}":
            braces -= 1
        elif ch == "[":
            brackets += 1
        elif ch == "]":
            brackets -= 1

    # Close structures
    text += "]" * max(0, brackets)
    text += "}" * max(0, braces)

    # Remove trailing comma before closing if present
    # (e.g., {"key": "val",} -> {"key": "val"})
    text = text.rstrip()
    if text.endswith(","):
        text = text[:-1]

    return text
