"""Tests for models.py — serialization, defaults, helpers."""

import json
import pytest
from resagent.models import (
    ResearchState, ResearchRun, Artifact, AgentTask, DecisionRecord,
    Observation, Budget, ArtifactType, Producer, TaskStatus, ActionName,
    AgentKind, TaskPriority, RunStatus,
)


class TestResearchRun:
    def test_defaults(self):
        run = ResearchRun(
            run_id="res-20260806-abc123",
            workspace_dir="/tmp/runs",
            research_goal="Test goal",
        )
        assert run.status == RunStatus.running
        assert run.created_at is not None

    def test_serialize(self):
        run = ResearchRun(
            run_id="res-20260806-abc123",
            workspace_dir="/tmp/runs",
            research_goal="Test goal",
        )
        data = json.loads(run.model_dump_json())
        assert data["run_id"] == "res-20260806-abc123"
        assert data["status"] == "running"


class TestArtifact:
    def test_create(self):
        a = Artifact(
            id="artifact_001",
            type=ArtifactType.scientific_decision,
            producer=Producer.ExpAgent,
            path="expagent/decision_001/scientific_decision.yaml",
            summary="Test decision",
        )
        assert a.id == "artifact_001"
        assert a.producer == Producer.ExpAgent


class TestAgentTask:
    def test_defaults(self):
        t = AgentTask(
            id="task_001",
            agent=Producer.CodingAgent,
            kind=AgentKind.coding_task,
        )
        assert t.status == TaskStatus.pending
        assert t.priority == TaskPriority.medium
        assert t.attempts == []
        assert t.input == {}

    def test_serialize(self):
        t = AgentTask(
            id="task_001",
            agent=Producer.CodingAgent,
            kind=AgentKind.coding_task,
            input={"repo_path": "/tmp/test"},
        )
        data = json.loads(t.model_dump_json())
        assert data["input"]["repo_path"] == "/tmp/test"


class TestDecisionRecord:
    def test_create(self):
        d = DecisionRecord(
            id="decision_001",
            made_by="ResAgent",
            reason="Testing",
            selected_action="call_exp_agent",
            alternatives=["call_coding_agent"],
            evidence=["artifact_001"],
        )
        assert d.made_by == "ResAgent"


class TestObservation:
    def test_create(self):
        o = Observation(
            action=ActionName.call_exp_agent,
            result="ok",
            detail="Test observation",
        )
        assert o.action == ActionName.call_exp_agent


class TestBudget:
    def test_defaults(self):
        b = Budget()
        assert b.max_tasks == 20
        assert b.max_task_retries == 2
        assert b.api_calls_used == 0


class TestResearchState:
    def test_empty_state(self):
        run = ResearchRun(
            run_id="test-001",
            workspace_dir="/tmp/runs",
            research_goal="Test",
        )
        state = ResearchState(run=run)
        assert state.artifacts == []
        assert state.tasks == []
        assert state.decisions == []

    def test_find_task(self):
        run = ResearchRun(run_id="t1", workspace_dir="/tmp", research_goal="g")
        state = ResearchState(run=run)
        t = AgentTask(id="task_001", agent=Producer.CodingAgent, kind=AgentKind.coding_task)
        state.tasks.append(t)
        assert state.find_task("task_001") is not None
        assert state.find_task("nonexistent") is None

    def test_find_artifact(self):
        run = ResearchRun(run_id="t1", workspace_dir="/tmp", research_goal="g")
        state = ResearchState(run=run)
        a = Artifact(id="a1", type=ArtifactType.report, producer=Producer.ResAgent, path="p")
        state.artifacts.append(a)
        assert state.find_artifact("a1") is not None
        assert state.find_artifact("nonexistent") is None

    def test_counters(self):
        run = ResearchRun(run_id="t1", workspace_dir="/tmp", research_goal="g")
        state = ResearchState(run=run)
        assert state.next_task_number() == 1
        assert state.next_decision_number() == 1
        assert state.next_artifact_number() == 1

    def test_full_serialize(self):
        run = ResearchRun(
            run_id="res-test-001",
            workspace_dir="/tmp/runs",
            research_goal="Integration test",
        )
        state = ResearchState(
            run=run,
            current_summary="Testing",
            artifacts=[
                Artifact(
                    id="a1", type=ArtifactType.scientific_decision,
                    producer=Producer.ExpAgent, path="p1", summary="s1",
                )
            ],
            tasks=[
                AgentTask(
                    id="t1", agent=Producer.CodingAgent,
                    kind=AgentKind.coding_task,
                )
            ],
            decisions=[
                DecisionRecord(
                    id="d1", made_by="ResAgent", reason="test",
                    selected_action="call_exp_agent",
                )
            ],
            observations=[
                Observation(action=ActionName.call_exp_agent, result="ok", detail="test")
            ],
            budget=Budget(max_tasks=10),
        )
        data = json.loads(state.model_dump_json())
        assert data["run"]["run_id"] == "res-test-001"
        assert len(data["artifacts"]) == 1
        assert len(data["tasks"]) == 1
        assert data["budget"]["max_tasks"] == 10


def test_next_task_number_is_global_across_agents():
    state = ResearchState(run=ResearchRun(run_id="task-ids", workspace_dir="/tmp", research_goal="goal"))
    state.tasks.extend([
        AgentTask(id="task_001", agent=Producer.ExpAgent, kind=AgentKind.advise),
        AgentTask(id="task_002", agent=Producer.CodingAgent, kind=AgentKind.coding_task),
        AgentTask(id="task_007", agent=Producer.ReproAgent, kind=AgentKind.repro_task),
    ])

    assert state.next_task_number() == 8
