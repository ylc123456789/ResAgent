import sys, os, json, time, glob
sys.path.insert(0, "/root/autodl-tmp/projects/ResAgent/src")
from resagent.config import Config, load_config
from resagent.orchestrator import init_run, build_controller
from resagent.persistence.state import save_state
from resagent.models import ActionName

cfg = load_config("/root/autodl-tmp/projects/ResAgent/config.yaml")
cfg.chat.default_advance_steps = 5
cfg.chat.max_steps_per_turn = 10

state = init_run(
    goal="Reproduce MNIST CNN from https://github.com/pytorch/examples.git (mnist subdir). Run 3 epochs. Verify >95% test accuracy.",
    workspace_root="/root/autodl-tmp/resagent-workspace/runs", config=cfg,
)
save_state(state)
run_id = state.run.run_id
print(f"Run: {run_id}")
ctrl = build_controller(cfg, mock=False)
t0 = time.time()

for step_n in range(1, 13):
    obs = ctrl.step(state)
    save_state(state)
    dt = time.time() - t0
    print(f"Step {step_n} ({dt:.0f}s): {obs.action.value} -> {obs.result}")
    if obs.result == "error":
        print(f"  ERR: {obs.detail[:200]}")
    if obs.action == ActionName.finish:
        break
    if obs.result == "user_response_required":
        pq = state.pending_question
        qtext = pq.text[:200] if pq else "?"
        print(f"  Q: {qtext}")
        break

print(f"\nStatus: {state.run.status.value}  Total: {time.time()-t0:.0f}s")
for t in state.tasks:
    print(f"  [{t.status.value}] {t.id} {t.agent.value}/{t.kind.value} attempts={len(t.attempts)}")

# GPU torch check
print("\n=== GPU torch? ===")
for f in glob.glob(f"/root/autodl-tmp/resagent-workspace/runs/{run_id}/tasks/reproagent/*/attempt_*/repo_workspace/logs/environment_audit.stdout"):
    d = json.load(open(f))
    t = d.get("torch",{})
    print(f"  CUDA: {t.get('cuda_available')}, devices: {t.get('device_count')}, ver: {t.get('version')}")

# Dataset cache
print("\n=== Dataset cache? ===")
for f in glob.glob(f"/root/autodl-tmp/resagent-workspace/runs/{run_id}/tasks/reproagent/*/attempt_*/repo_workspace/logs/experiment_*stderr"):
    content = open(f).read()
    if "Downloading" in content:
        print("  Downloading from internet (no cache)")
    else:
        print("  No download detected (cached or pre-existing)")
    break
