"""The bounded-scope enforcer in the cloud acceptance harness.

Third bug class found in this helper — it earns a committed test.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cloud_acceptance import _enforce_bounded_scope  # noqa: E402

from resagent.models import (  # noqa: E402
    AgentKind, AgentTask, Producer, TaskStatus,
)
from resagent.persistence.state import init_state  # noqa: E402


def _task(tid, agent, capability, *, source="", action_id="",
          status=TaskStatus.pending, depends_on=None):
    return AgentTask(
        id=tid, agent=agent, kind=AgentKind.advise, capability=capability,
        required=True, source=source, action_id=action_id, status=status,
        depends_on=depends_on or [],
    )


def test_mid_flight_repair_is_kept(tmp_path):
    """blocked -> repair flow: the repair task is created AFTER the planned
    analysis task but repairs the kept experiment — it is in scope."""
    state = init_state("scope-repair", str(tmp_path), "goal")
    state.tasks.extend([
        _task("task_001", Producer.ExpAgent, "", action_id="initial_consult",
              status=TaskStatus.completed),
        _task("task_002", Producer.ReproAgent, "execute_experiment",
              status=TaskStatus.blocked),
        _task("task_003", Producer.ExpAgent, "analyze_results",
              depends_on=["task_002"]),
        # repair created later (list order after the analysis), sourced at
        # the blocked experiment — must be kept
        _task("task_004", Producer.CodingAgent, "modify_code",
              source="task_002", action_id="repair_task_002"),
    ])
    _enforce_bounded_scope(state)
    assert state.find_task("task_004").status == TaskStatus.pending
    assert state.find_task("task_002").status == TaskStatus.blocked


def test_followup_wave_patch_is_declined(tmp_path):
    """A modify_code proposed by a follow-up decision (source = decision id,
    not the experiment task) is scope expansion and is declined."""
    state = init_state("scope-wave", str(tmp_path), "goal")
    state.tasks.extend([
        _task("task_001", Producer.ExpAgent, "", action_id="initial_consult",
              status=TaskStatus.completed),
        _task("task_002", Producer.ReproAgent, "execute_experiment",
              status=TaskStatus.completed),
        _task("task_003", Producer.ExpAgent, "analyze_results",
              depends_on=["task_002"], status=TaskStatus.completed),
        _task("task_004", Producer.ReproAgent, "execute_experiment",
              source="exp_decision_003"),
        _task("task_005", Producer.CodingAgent, "modify_code",
              source="exp_decision_003"),
    ])
    _enforce_bounded_scope(state)
    assert state.find_task("task_004").status == TaskStatus.skipped
    assert state.find_task("task_005").status == TaskStatus.skipped
    # committed work untouched
    assert state.find_task("task_002").status == TaskStatus.completed
    assert state.find_task("task_003").status == TaskStatus.completed


def test_second_experiment_in_initial_graph_is_declined(tmp_path):
    state = init_state("scope-two-exp", str(tmp_path), "goal")
    state.tasks.extend([
        _task("task_001", Producer.ExpAgent, "", action_id="initial_consult",
              status=TaskStatus.completed),
        _task("task_002", Producer.ReproAgent, "execute_experiment"),
        _task("task_003", Producer.ReproAgent, "execute_experiment"),
        _task("task_004", Producer.ExpAgent, "analyze_results",
              depends_on=["task_002", "task_003"]),
    ])
    _enforce_bounded_scope(state)
    assert state.find_task("task_002").status == TaskStatus.pending
    assert state.find_task("task_003").status == TaskStatus.skipped
    assert state.find_task("task_004").status == TaskStatus.pending
