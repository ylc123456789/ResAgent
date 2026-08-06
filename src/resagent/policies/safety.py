"""Safety policy — user confirmation for expensive or destructive actions."""

from __future__ import annotations


class SafetyPolicy:
    """Gate-keeps actions that the user should approve first."""

    def __init__(
        self,
        confirm_before_external_runs: bool = True,
        confirm_before_long_tasks: bool = True,
    ):
        self.confirm_before_external_runs = confirm_before_external_runs
        self.confirm_before_long_tasks = confirm_before_long_tasks

    def needs_confirmation(self, task_kind: str, estimated_minutes: int = 0) -> str:
        """Return reason if confirmation is needed, empty string otherwise."""
        if task_kind == "repro_task" and self.confirm_before_external_runs:
            return "External reproduction task requires confirmation."
        if estimated_minutes > 30 and self.confirm_before_long_tasks:
            return (
                f"Task estimated at {estimated_minutes} minutes "
                f"requires confirmation."
            )
        return ""
