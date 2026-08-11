"""Comprehensive cloud test — all 4 modules, session resume, retry, env reuse."""
import sys, os, json, time, subprocess, textwrap, glob as g
sys.path.insert(0, "/root/autodl-tmp/projects/ResAgent/src")

from resagent.config import load_config, Config
from resagent.orchestrator import init_run, build_controller, resume_run
from resagent.state import save_state, load_state, submit_user_response
from resagent.models import ActionName, Producer, AgentKind
from resagent.workspace_layout import WorkspaceLayout

PASSED = 0; FAILED = 0
WORKSPACE_ROOT = "/root/autodl-tmp/resagent-workspace"
RUNS = WORKSPACE_ROOT + "/runs"
CFG = load_config("/root/autodl-tmp/projects/ResAgent/config.yaml")
CFG.chat.default_advance_steps = 5
CFG.chat.max_steps_per_turn = 10

def test(name, fn):
    global PASSED, FAILED
    print(f"\n{'='*60}\nTEST: {name}\n{'='*60}")
    t0 = time.time()
    try:
        fn()
        print(f"RESULT: PASS ({time.time()-t0:.0f}s)")
        PASSED += 1
    except Exception as e:
        print(f"RESULT: FAIL ({time.time()-t0:.0f}s)")
        import traceback; traceback.print_exc()
        FAILED += 1

def setup_repo():
    repo = os.path.join(WORKSPACE_ROOT, "comprehensive-repo")
    subprocess.run(["rm", "-rf", repo])
    os.makedirs(repo)
    code = textwrap.dedent("""\
import json, random
NUM_EPOCHS = 10; LEARNING_RATE = 0.01
def train(epochs=NUM_EPOCHS, lr=LEARNING_RATE):
    results = {"epochs": [], "final_accuracy": 0.0}
    accuracy = 0.5
    for epoch in range(1, epochs + 1):
        accuracy += random.uniform(0.03, 0.08)
        accuracy = min(accuracy, 1.0)
        results["epochs"].append({"epoch": epoch, "accuracy": round(accuracy, 4)})
        print(f"Epoch {epoch}/{epochs} done")
    results["final_accuracy"] = round(accuracy, 4)
    with open("results.json", "w") as f:
        json.dump(results, f, indent=2)
if __name__ == "__main__":
    train()
""")
    with open(os.path.join(repo, "train.py"), "w") as f:
        f.write(code)
    for cmd in [
        ["git", "init"], ["git", "config", "user.email", "t@x"],
        ["git", "config", "user.name", "T"],
        ["git", "add", "train.py"], ["git", "commit", "-m", "init"],
    ]:
        subprocess.run(cmd, cwd=repo, capture_output=True)
    return repo

# ═══════════════════════════════════════════════════════════════════════
# Test 1: CodingAgent + ExpAgent + code modification
# ═══════════════════════════════════════════════════════════════════════
def test_codingagent():
    repo = setup_repo()
    state = init_run(
        goal=f"Add per-epoch loss logging to {repo}/train.py. Loss = 1.0 - accuracy + noise. Do NOT change accuracy logic.",
        workspace_root=RUNS, config=CFG,
    )
    save_state(state)
    run_id = state.run.run_id
    ctrl = build_controller(CFG, mock=False)
    for _ in range(8):
        obs = ctrl.step(state)
        save_state(state)
        if obs.action == ActionName.finish:
            break
    with open(os.path.join(repo, "train.py")) as f:
        assert "loss" in f.read().lower(), "code should contain loss"
    print(f"  Run: {run_id}")
    print(f"  Tasks: {len(state.tasks)}, Artifacts: {len(state.artifacts)}")
    print(f"  Steps: {len(state.observations)}, Status: {state.run.status.value}")
    # Check session cards
    for root, dirs, files in os.walk(os.path.join(RUNS, run_id, "tasks")):
        for fn in files:
            if fn == "session.yaml":
                print(f"  session: {os.path.relpath(os.path.join(root,fn), RUNS)}")
    return run_id

# ═══════════════════════════════════════════════════════════════════════
# Test 2: ReproAgent — paper-level reproduction (uses arxiv paper)
# ═══════════════════════════════════════════════════════════════════════
def test_reproagent_with_paper():
    state = init_run(
        goal=(
            "Reproduce the Neural ODE MNIST experiment from the paper "
            "https://arxiv.org/abs/1806.07366 using the repository "
            "https://github.com/rtqichen/torchdiffeq.git. "
            "Run odenet_mnist.py with 5 epochs. Report test accuracy."
        ),
        workspace_root=RUNS, config=CFG,
    )
    save_state(state)
    run_id = state.run.run_id
    ctrl = build_controller(CFG, mock=False)
    for step_n in range(1, 16):
        obs = ctrl.step(state)
        save_state(state)
        print(f"  Step {step_n}: {obs.action.value} -> {obs.result}")
        if obs.result == "error":
            print(f"    ERR: {obs.detail[:150]}")
        if obs.action == ActionName.finish:
            break
        if obs.result == "user_response_required":
            print(f"    PAUSED: {state.pending_question.text[:150] if state.pending_question else '?'}")
            break
    print(f"  Run: {run_id}, Status: {state.run.status.value}")
    print(f"  Tasks: {len(state.tasks)}, Artifacts: {len(state.artifacts)}")
    # Check session cards
    for root, dirs, files in os.walk(os.path.join(RUNS, run_id, "tasks")):
        for fn in files:
            if fn == "session.yaml":
                with open(os.path.join(root, fn)) as f:
                    content = f.read()
                if "key_artifacts" in content and "scientific_decision" in content:
                    print(f"  ✓ session card has key_artifacts")
                    break
    # Check env
    for f in g.glob(f"{RUNS}/{run_id}/tasks/reproagent/*/attempt_*/repo_workspace/session.yaml"):
        with open(f) as fh:
            card = fh.read()
        if "conda_env" in card:
            for line in card.split("\n"):
                if "conda_env" in line:
                    print(f"  {line.strip()}")
    return run_id

# ═══════════════════════════════════════════════════════════════════════
# Test 3: ask_user pause + resume
# ═══════════════════════════════════════════════════════════════════════
def test_ask_user_pause_resume():
    repo = setup_repo()
    state = init_run(
        goal=f"In {repo}/train.py, the variable NUM_EPOCHS controls training length. Change it from 10 to 20. Verify the change.",
        workspace_root=RUNS, config=CFG,
    )
    save_state(state)
    run_id = state.run.run_id
    ctrl = build_controller(CFG, mock=False)

    # Run until paused
    for _ in range(8):
        obs = ctrl.step(state)
        save_state(state)
        if obs.result == "user_response_required":
            break

    pq = state.pending_question
    assert pq is not None, "should have pending question"
    assert state.run.status.value == "paused"
    print(f"  Paused with question: {pq.text[:150]}")
    print(f"  Question ID: {pq.question_id}")

    # Answer and resume
    submit_user_response(state, pq.question_id, "Yes, change NUM_EPOCHS from 10 to 20 in train.py")
    save_state(state)
    assert state.pending_question is None
    assert state.run.status.value == "running"
    print(f"  Resumed: pending_question cleared, status=running")

    # Continue to completion
    for _ in range(8):
        obs = ctrl.step(state)
        save_state(state)
        if obs.action == ActionName.finish:
            break

    print(f"  Run: {run_id}, Status: {state.run.status.value}")
    with open(os.path.join(repo, "train.py")) as f:
        code = f.read()
    if "20" in code:
        print(f"  ✓ NUM_EPOCHS changed to 20")
    return run_id

# ═══════════════════════════════════════════════════════════════════════
# Test 4: Project-level env reuse — second ReproTask in same run
# ═══════════════════════════════════════════════════════════════════════
def test_env_reuse():
    state = init_run(
        goal=(
            "First: reproduce MNIST CNN from https://github.com/pytorch/examples.git "
            "(mnist subdir) with 2 epochs. Second: run it again with 3 epochs to verify consistency. "
            "Both should use the same conda environment."
        ),
        workspace_root=RUNS, config=CFG,
    )
    save_state(state)
    run_id = state.run.run_id
    ctrl = build_controller(CFG, mock=False)

    for step_n in range(1, 20):
        obs = ctrl.step(state)
        save_state(state)
        print(f"  Step {step_n}: {obs.action.value} -> {obs.result}")
        if obs.result == "error":
            print(f"    ERR: {obs.detail[:120]}")
        if obs.action == ActionName.finish:
            break
        if obs.result == "user_response_required":
            break

    print(f"  Run: {run_id}, Status: {state.run.status.value}")
    # Check how many distinct conda envs were created
    envs = set()
    for f in g.glob(f"{RUNS}/{run_id}/tasks/reproagent/*/attempt_*/repo_workspace/session.yaml"):
        with open(f) as fh:
            for line in fh:
                if "conda_env:" in line:
                    envs.add(line.strip())
    print(f"  Distinct conda envs: {len(envs)}")
    for e in envs:
        print(f"    {e}")
    repro_count = sum(1 for t in state.tasks if t.agent == Producer.ReproAgent and t.status.value == "completed")
    print(f"  Completed ReproAgent tasks: {repro_count}")
    return run_id

# ═══════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print(f"=== COMPREHENSIVE CLOUD TEST === {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Workspace: {WORKSPACE_ROOT}")

    test("CodingAgent + ExpAgent code modification", test_codingagent)
    test("ReproAgent with arxiv paper reference", test_reproagent_with_paper)
    test("ask_user pause + resume", test_ask_user_pause_resume)
    test("Project-level env reuse (multi ReproTask)", test_env_reuse)

    print(f"\n{'='*60}")
    print(f"SUMMARY: {PASSED}/{PASSED+FAILED} passed")
    print(f"All artifacts: {RUNS}/")
