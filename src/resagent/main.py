"""ResAgent CLI — entry point for all commands.

Usage:
    resagent chat [--workspace <dir>] [--conversation-id <id>] [--mock]
    resagent init --goal <file> --workspace <dir>
    resagent run --workspace <dir> --run-id <id> [--mock] [--max-steps N]
    resagent step --workspace <dir> --run-id <id> [--mock]
    resagent status --workspace <dir> --run-id <id>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config
from .orchestrator import init_run, build_controller, run_loop, resume_run, status
from .persistence.state import save_state, submit_user_response
from .persistence.report import generate_all


def main():
    parser = argparse.ArgumentParser(
        prog="resagent",
        description="Research Project Manager / Orchestrator",
    )

    # Shared optional arguments for all commands that need module paths
    def _add_path_args(p):
        p.add_argument("--expagent-path", default="",
                       help="Path to ExpAgent source (overrides env/config)")
        p.add_argument("--codingagent-path", default="",
                       help="Path to CodingAgent source (overrides env/config)")
        p.add_argument("--reproagent-path", default="",
                       help="Path to ReproAgent source (overrides env/config)")
        p.add_argument("--config", default="", help="Path to config.yaml")

    sub = parser.add_subparsers(dest="command", required=True)

    # -- init --
    p_init = sub.add_parser("init", help="Create a new research run")
    p_init.add_argument("--goal", required=True, help="Path to idea.md or goal text")
    p_init.add_argument("--workspace", default="",
                        help="Workspace root dir (default: $RESAGENT_WORKSPACE > config.yaml > ./runs)")
    _add_path_args(p_init)

    # -- run --
    p_run = sub.add_parser("run", help="Run the agentic loop")
    p_run.add_argument("--workspace", required=True, help="Workspace root dir")
    p_run.add_argument("--run-id", required=True, help="Run ID")
    p_run.add_argument("--goal", default="", help="Research goal (creates new run if needed)")
    p_run.add_argument("--mock", action="store_true",
                       help="Mock all adapters + LLM (no API calls)")
    p_run.add_argument("--max-steps", type=int, default=30, help="Max loop steps")
    _add_path_args(p_run)

    # -- step --
    p_step = sub.add_parser("step", help="Execute one loop step")
    p_step.add_argument("--workspace", required=True, help="Workspace root dir")
    p_step.add_argument("--run-id", required=True, help="Run ID")
    p_step.add_argument("--mock", action="store_true",
                       help="Mock all adapters + LLM")
    _add_path_args(p_step)

    # -- status --
    p_status = sub.add_parser("status", help="Show run status")
    p_status.add_argument("--workspace", required=True, help="Workspace root dir")
    p_status.add_argument("--run-id", required=True, help="Run ID")

    # -- answer --
    p_answer = sub.add_parser("answer", help="Answer a paused run question and resume")
    p_answer.add_argument("--workspace", required=True, help="Workspace root dir")
    p_answer.add_argument("--run-id", required=True, help="Run ID")
    p_answer.add_argument("--question-id", required=True, help="Pending question ID")
    p_answer.add_argument("--response", required=True, help="User response text")
    p_answer.add_argument("--mock", action="store_true", help="Mock all adapters + LLM")
    p_answer.add_argument("--max-steps", type=int, default=30, help="Max loop steps after resuming")
    _add_path_args(p_answer)
    # -- chat --
    p_chat = sub.add_parser("chat", help="Unified conversation entry (REPL)")
    p_chat.add_argument("--workspace", default="",
                        help="Workspace root dir (default: $RESAGENT_WORKSPACE > config.yaml > ./runs)")
    p_chat.add_argument("--conversation-id", default="",
                        help="Resume an existing conversation by ID")
    p_chat.add_argument("--resume", default="", help="Alias for --conversation-id")
    p_chat.add_argument("--mock", action="store_true",
                        help="Mock LLM + all experts (no API calls)")
    _add_path_args(p_chat)

    args = parser.parse_args()

    try:
        _dispatch(args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


def _dispatch(args):
    cfg = load_config(getattr(args, "config", ""))

    # CLI args override config
    if getattr(args, "expagent_path", ""):
        cfg.agents.expagent = args.expagent_path
    if getattr(args, "codingagent_path", ""):
        cfg.agents.codingagent = args.codingagent_path
    if getattr(args, "reproagent_path", ""):
        cfg.agents.reproagent = args.reproagent_path

    if args.command == "init":
        goal = _read_goal(args.goal)
        workspace = args.workspace or cfg.workspace.default_runs_dir
        state = init_run(goal=goal, workspace_root=workspace, config=cfg)
        generate_all(state)
        print(f"Created run: {state.run.run_id}")
        print(f"Workspace: {state.run.workspace_dir}/{state.run.run_id}")

    elif args.command == "run":
        mock = getattr(args, "mock", False)
        state = resume_run(args.workspace, args.run_id)

        if state is None and args.goal:
            state = init_run(
                goal=_read_goal(args.goal),
                workspace_root=args.workspace,
                config=cfg,
            )
            state.run.run_id = args.run_id
            save_state(state)

        if state is None:
            print(f"No run found and no --goal provided.")
            sys.exit(1)

        ctrl = build_controller(cfg, mock=mock)
        state = run_loop(state, ctrl, max_steps=args.max_steps)
        generate_all(state)
        print(f"Run finished: {state.run.status.value}")

    elif args.command == "step":
        mock = getattr(args, "mock", False)
        state = resume_run(args.workspace, args.run_id)
        if state is None:
            print(f"No run found: {args.workspace}/{args.run_id}")
            sys.exit(1)
    elif args.command == "answer":
        state = resume_run(args.workspace, args.run_id)
        if state is None:
            print(f"No run found: {args.workspace}/{args.run_id}")
            sys.exit(1)
        submit_user_response(state, args.question_id, args.response)
        ctrl = build_controller(cfg, mock=args.mock)
        state = run_loop(state, ctrl, max_steps=args.max_steps)
        save_state(state)
        generate_all(state)
        print(f"Run status: {state.run.status.value}")

    elif args.command == "status":
        print(status(args.workspace, args.run_id))

    elif args.command == "chat":
        from .capabilities import CapabilityRegistry
        from .conversation.loop import ChatLoop, run_repl
        from .conversation.tools import ChatTools
        from .conversation.history import load_conversation, new_conversation

        mock = getattr(args, "mock", False)
        ws = str(Path(args.workspace or cfg.workspace.default_runs_dir).resolve())

        registry = CapabilityRegistry(cfg)
        registry.load()
        for w in registry.warnings:
            print(f"[registry] {w}", file=sys.stderr)

        # One shared controller: its adapters serve Tier-1 consults, and the
        # controller itself drives Tier-2 run advancement.
        ctrl = build_controller(cfg, mock=mock)
        tools = ChatTools(
            cfg, registry,
            expagent=ctrl.expagent,
            codingagent=ctrl.codingagent,
            controller_factory=lambda: ctrl,
            reproagent=ctrl.reproagent,
            mock=mock,
        )
        chat = ChatLoop(cfg, registry, tools, mock=mock)

        conv_id = args.conversation_id or args.resume
        conv = load_conversation(ws, conv_id) if conv_id else None
        if conv is None:
            if conv_id:
                print(f"Conversation not found: {conv_id}; starting a new one.")
            conv = new_conversation(ws, cfg.chat.conversations_dirname)

        run_repl(conv, chat, tools, ws)


def _read_goal(goal_spec: str) -> str:
    p = Path(goal_spec)
    if p.exists() and p.is_file():
        return p.read_text(encoding="utf-8").strip()
    return goal_spec
