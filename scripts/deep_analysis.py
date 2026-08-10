"""Deep analysis of the repro test run."""
import json, glob, os

RUNDIR = "/root/autodl-tmp/resagent-workspace/runs/res-20260810-18266a"

# 1. ResAgent controller view
print("=" * 60)
print("1. RESAGENT CONTROLLER VIEW")
print("=" * 60)
with open(f"{RUNDIR}/state.json") as f:
    s = json.load(f)
print(f"Status: {s['run']['status']}")
for t in s["tasks"]:
    errs = []
    for a in t.get("attempts", []):
        e = a.get("error", "")
        if e:
            errs.append(f"attempt_{a['attempt_number']}: {e[:120]}")
    print(f"  [{t['status']}] {t['id']} {t['agent']}/{t['kind']} attempts={len(t.get('attempts',[]))}")
    for e in errs:
        print(f"    {e}")
print("\nObservations:")
for o in s["observations"]:
    print(f"  [{o['action']}] {o['result']}: {o['detail'][:150]}")
print(f"\nBudget: tasks={s['budget']['tasks_run']}, api_calls={s['budget']['api_calls_used']}")

# 2. ReproAgent attempt_001 (failed)
print("\n" + "=" * 60)
print("2. REPROAGENT ATTEMPT_001 (FAILED)")
print("=" * 60)
a1_state = f"{RUNDIR}/tasks/reproagent/task_001/attempt_001/repo_workspace/state.json"
if os.path.exists(a1_state):
    with open(a1_state) as f:
        d = json.load(f)
    print(f"Status: {d.get('status','?')}")
    for st in d.get("steps", []):
        err = st.get("error", "")
        print(f"  step {st['step']}: {st['action']} {err[:100] if err else 'ok'}")
        for cr in st.get("command_results", []):
            if cr.get("returncode") != 0:
                print(f"    FAIL: {cr.get('command','')[:100]} -> rc={cr.get('returncode')}")

# 3. ReproAgent attempt_002 (current)
print("\n" + "=" * 60)
print("3. REPROAGENT ATTEMPT_002 (CURRENT)")
print("=" * 60)
a2_state = f"{RUNDIR}/tasks/reproagent/task_001/attempt_002/repo_workspace/state.json"
if os.path.exists(a2_state):
    with open(a2_state) as f:
        d = json.load(f)
    print(f"Status: {d.get('status','?')}")
    for st in d.get("steps", []):
        act = st.get("action", "?")
        print(f"  step {st['step']}: {act} (stage={st.get('stage_hint','')})")
        for cr in st.get("command_results", []):
            rc = cr.get("returncode", -1)
            dur = cr.get("duration_seconds", 0)
            cmd = cr.get("command", "")[:100]
            ok = "ok" if rc == 0 else f"FAIL(rc={rc})"
            print(f"    {ok} {dur:.0f}s: {cmd}")

# 4. Environment audit
print("\n" + "=" * 60)
print("4. GPU & ENVIRONMENT")
print("=" * 60)
for f in glob.glob(f"{RUNDIR}/tasks/reproagent/*/attempt_002/repo_workspace/logs/environment_audit.stdout"):
    d = json.load(open(f))
    t = d.get("torch", {})
    print(f"CUDA available: {t.get('cuda_available')}")
    print(f"CUDA compiled: {t.get('cuda_compiled')}")
    print(f"Device count: {t.get('device_count')}")
    print(f"Torch version: {t.get('version')}")

# 5. LLM reasoning
print("\n" + "=" * 60)
print("5. REPROAGENT LLM DECISIONS")
print("=" * 60)
resp_files = sorted(glob.glob(f"{RUNDIR}/tasks/reproagent/*/attempt_002/repo_workspace/logs/llm_*response.txt"))
for rf in resp_files[-4:]:
    with open(rf) as f:
        d = json.load(f)
    action = d.get("action", "?")
    thinking = d.get("thinking", "")[:200]
    step = os.path.basename(rf).split("_step_")[1].replace(".response.txt", "")
    print(f"  step_{step}: action={action}")
    print(f"    {thinking}")

# 6. Experiment output
print("\n" + "=" * 60)
print("6. EXPERIMENT OUTPUT")
print("=" * 60)
for f in sorted(glob.glob(f"{RUNDIR}/tasks/reproagent/*/attempt_002/repo_workspace/logs/experiment_*stdout")):
    size = os.path.getsize(f)
    if size > 0:
        name = os.path.basename(f)
        with open(f) as fh:
            content = fh.read()[:500]
        print(f"  {name} ({size}B): {content[:300]}")
    else:
        print(f"  {os.path.basename(f)}: EMPTY ({size}B)")

for f in sorted(glob.glob(f"{RUNDIR}/tasks/reproagent/*/attempt_002/repo_workspace/logs/experiment_*stderr")):
    size = os.path.getsize(f)
    if size > 0:
        name = os.path.basename(f)
        with open(f) as fh:
            content = fh.read()[:500]
        print(f"  {name} ({size}B): {content[:300]}")
