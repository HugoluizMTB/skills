#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("GRAPH_HOME", "~/.claude/graph")).expanduser()
SPECS = HOME / "specs"
ACTIVE = HOME / "active"
PENDING = HOME / "pending.json"
TAG = re.compile(r"^\[([A-Za-z0-9_.-]+)\]")
WAIT_MARKERS = ("WAITING:", "DECISION:", "BLOCKED:")
MAX_NUDGES = 3
CHECK_TIMEOUT = 540


def load(path):
    return json.loads(Path(path).read_text())


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


def state_path(spec_path):
    return Path(str(spec_path).removesuffix(".json") + ".state.json")


def nodes(spec):
    return {t["id"]: t for t in spec["tasks"]}


def ready(spec, state):
    done = set(state["done"])
    return [
        t
        for t in spec["tasks"]
        if t["id"] not in done and all(d in done for d in t.get("depends_on", []))
    ]


def rules_text(spec):
    rules = list(spec.get("rules", []))
    if not spec.get("commit", False):
        rules.append("Do not commit or push. Leave everything in the working tree.")
    return "\n".join(f"- {r}" for r in rules) or "- (none)"


def node_text(spec, node):
    checks = node.get("done_when", [])
    check_lines = "\n".join(f"  - `{c}`" for c in checks) or "  - manual check described in the goal"
    mode = node.get("mode", "session")
    how = (
        "Delegate to ONE agent (Agent tool) with this whole block as the prompt; check its result here when it returns."
        if mode == "agent"
        else "Do it in this session."
    )
    return (
        f"### [{node['id']}] {node['title']}\n"
        f"Goal: {node['goal']}\n"
        f"{node.get('spec', '').strip()}\n"
        f"Done when (the hook runs this when the task is marked completed):\n{check_lines}\n"
        f"How: {how}"
    )


def injection(spec, state, header):
    nxt = ready(spec, state)
    done = set(state["done"])
    later = [t for t in spec["tasks"] if t["id"] not in done and t not in nxt]
    parts = [header]
    if not nxt and not later:
        parts.append("Graph finished. Every task passed. Report to the user in a few lines.")
        return "\n\n".join(parts)
    current = nxt[0] if nxt else None
    if current:
        state["current"] = current["id"]
        parts.append("CURRENT TASK (mark its TUI task in_progress now):\n" + node_text(spec, current))
    rest = [t for t in spec["tasks"] if t["id"] not in done and (not current or t["id"] != current["id"])]
    if rest:
        parts.append(
            "Later (titles only; each spec arrives when the previous task passes):\n"
            + "\n".join(f"- [{t['id']}] {t['title']}" for t in rest)
        )
    parts.append(
        "Graph rules:\n"
        + rules_text(spec)
        + "\n- Only stop with a line starting with WAITING: (CI/build/deploy), DECISION: (question for the user) or BLOCKED: (real blocker)."
    )
    return "\n\n".join(parts)


def active_for(session):
    path = ACTIVE / f"{session}.json"
    if path.exists():
        return load(path)
    if session and PENDING.exists():
        pending = load(PENDING)
        if time.time() - pending.get("at", 0) < 1800:
            save(path, {"spec": pending["spec"]})
            PENDING.unlink()
            return {"spec": pending["spec"]}
    return None


def read_hook():
    try:
        return json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return {}


def cmd_start(args):
    spec_path = Path(args[0]).expanduser().resolve()
    session = args[1] if len(args) > 1 else ""
    spec = load(spec_path)
    ids = [t["id"] for t in spec["tasks"]]
    for t in spec["tasks"]:
        for d in t.get("depends_on", []):
            if d not in ids:
                sys.exit(f"task {t['id']} depends on {d}, which does not exist")
    sp = state_path(spec_path)
    state = load(sp) if sp.exists() else {"done": [], "nudges": {}, "log": []}
    if session and not session.startswith("$"):
        save(ACTIVE / f"{session}.json", {"spec": str(spec_path)})
    else:
        save(PENDING, {"spec": str(spec_path), "at": time.time()})
    text = injection(spec, state, f"GRAPH ACTIVE: {spec['name']}\nGoal: {spec['goal']}")
    save(sp, state)
    todo = [t for t in spec["tasks"] if t["id"] not in state["done"]]
    print(text)
    print("\nCREATE NOW these TUI tasks (TaskCreate), in this order, with exactly these subjects:")
    for t in todo:
        print(f"- [{t['id']}] {t['title']}")


def cmd_status(args):
    for f in sorted(SPECS.glob("*.state.json")):
        st = load(f)
        spec = load(str(f).replace(".state.json", ".json"))
        print(f"{spec['name']}: {len(st['done'])}/{len(spec['tasks'])} done, current {st.get('current')}")


def cmd_stop(args):
    session = args[0] if args else ""
    for f in ACTIVE.glob("*.json"):
        if not session or f.stem == session:
            f.unlink()
    if PENDING.exists():
        PENDING.unlink()
    print("graph turned off for this session")


def context_for(hook):
    active = active_for(hook.get("session_id", ""))
    if not active:
        return None, None, None
    spec_path = Path(active["spec"])
    spec = load(spec_path)
    sp = state_path(spec_path)
    state = load(sp) if sp.exists() else {"done": [], "nudges": {}, "log": []}
    return spec, sp, state


def run_checks(spec, node):
    failures = []
    for check in node.get("done_when", []):
        try:
            out = subprocess.run(
                check,
                shell=True,
                cwd=os.path.expanduser(spec.get("cwd", ".")),
                capture_output=True,
                text=True,
                timeout=CHECK_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            failures.append(f"`{check}` took longer than {CHECK_TIMEOUT}s")
            continue
        if out.returncode != 0:
            tail = (out.stdout + out.stderr).strip().splitlines()[-12:]
            failures.append(f"`{check}` exited {out.returncode}:\n" + "\n".join(tail))
    return failures


def hook_task_completed(args):
    hook = read_hook()
    spec, sp, state = context_for(hook)
    if not spec:
        return
    m = TAG.match(hook.get("task_subject", ""))
    if not m or m.group(1) not in nodes(spec):
        return
    node = nodes(spec)[m.group(1)]
    blocked = [d for d in node.get("depends_on", []) if d not in state["done"]]
    if blocked:
        print(f"[{node['id']}] depends on {', '.join(blocked)}, which has not passed yet.", file=sys.stderr)
        sys.exit(2)
    failures = run_checks(spec, node)
    if failures:
        state["log"].append({"id": node["id"], "at": time.time(), "result": "falhou"})
        save(sp, state)
        print(f"[{node['id']}] is not done. Check failed:\n" + "\n\n".join(failures), file=sys.stderr)
        sys.exit(2)
    state.setdefault("tui", {})[str(hook.get("task_id", ""))] = node["id"]
    if node["id"] not in state["done"]:
        state["done"].append(node["id"])
    state["nudges"].pop(node["id"], None)
    state["log"].append({"id": node["id"], "at": time.time(), "result": "passou"})
    save(sp, state)


def hook_task_update(args):
    hook = read_hook()
    tool_input = hook.get("tool_input", {})
    if tool_input.get("status") != "completed":
        return
    spec, sp, state = context_for(hook)
    if not spec:
        return
    finished = state.get("tui", {}).get(str(tool_input.get("taskId", "")))
    if not finished or finished not in state["done"]:
        return
    text = injection(spec, state, f"[{finished}] passed its check.")
    save(sp, state)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}))


def hook_stop(args):
    hook = read_hook()
    spec, sp, state = context_for(hook)
    if not spec:
        return
    current = state.get("current")
    if not current or current in state["done"] or not ready(spec, state):
        return
    last = (hook.get("last_assistant_message") or "").strip()
    if any(marker in last for marker in WAIT_MARKERS):
        return
    if hook.get("background_tasks"):
        return
    count = state["nudges"].get(current, 0)
    if count >= MAX_NUDGES:
        return
    state["nudges"][current] = count + 1
    save(sp, state)
    node = nodes(spec)[current]
    reason = (
        f"Graph '{spec['name']}' is on task [{current}] {node['title']} and you stopped without saying why. "
        "Keep going. If you are waiting on CI/build/deploy, write a line 'WAITING: <what>'. "
        "If you need the user, 'DECISION: <question>'. If there is a real blocker, 'BLOCKED: <reason>'."
    )
    print(json.dumps({"decision": "block", "reason": reason}))


def hook_bash_guard(args):
    hook = read_hook()
    spec, _, _ = context_for(hook)
    if not spec or spec.get("commit", False):
        return
    command = (hook.get("tool_input") or {}).get("command", "")
    if re.search(r"\bgit\b[^|;&]*\b(commit|push)\b", command) or re.search(r"\bgh\s+pr\s+(create|merge)\b", command):
        print(
            f"Graph '{spec['name']}' does not allow commit, push or PRs. Leave the changes in the working tree.",
            file=sys.stderr,
        )
        sys.exit(2)


COMMANDS = {
    "start": cmd_start,
    "status": cmd_status,
    "stop": cmd_stop,
    "hook-task-completed": hook_task_completed,
    "hook-task-update": hook_task_update,
    "hook-stop": hook_stop,
    "hook-bash-guard": hook_bash_guard,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit("usage: graph.py start <spec.json> [session] | status | stop [session]")
    try:
        COMMANDS[sys.argv[1]](sys.argv[2:])
    except SystemExit:
        raise
    except Exception as error:
        if sys.argv[1].startswith("hook-"):
            print(f"graph hook error: {error}", file=sys.stderr)
            sys.exit(0)
        raise
