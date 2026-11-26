---
name: graph
description: Runs a multi-step job as a spec-driven task graph (LangGraph ideas + harness engineering). Asks questions grounded in the session, writes the spec as JSON, creates the tasks in the TUI and injects only the current task's spec; every completed task must pass a real check (hook) before the next one is released, and a Stop hook keeps the agent from stopping silently. Use when the user types /graph, asks to "run this as a graph", "work from a spec", "one task at a time", or wants a long job to keep moving without babysitting.
hooks:
  TaskCompleted:
    - hooks:
        - type: command
          command: python3 -I ~/.claude/skills/graph/graph.py hook-task-completed
          timeout: 600
  PostToolUse:
    - matcher: TaskUpdate
      hooks:
        - type: command
          command: python3 -I ~/.claude/skills/graph/graph.py hook-task-update
  PreToolUse:
    - matcher: Bash
      hooks:
        - type: command
          command: python3 -I ~/.claude/skills/graph/graph.py hook-bash-guard
  Stop:
    - hooks:
        - type: command
          command: python3 -I ~/.claude/skills/graph/graph.py hook-stop
---

# /graph

```
python3 -I ~/.claude/skills/graph/graph.py start <spec.json> ${CLAUDE_SESSION_ID}
python3 -I ~/.claude/skills/graph/graph.py status
python3 -I ~/.claude/skills/graph/graph.py decide ${CLAUDE_SESSION_ID} "<decision>"
python3 -I ~/.claude/skills/graph/graph.py report ${CLAUDE_SESSION_ID}
python3 -I ~/.claude/skills/graph/graph.py stop ${CLAUDE_SESSION_ID}
```

Specs live in `graph/specs/<slug>.json` inside the Claude profile the skill is installed in (`~/.claude/graph/specs/` by default; override with `GRAPH_HOME`). The state (what passed, retries) sits next to it in `<slug>.state.json`. The state is the checkpoint: running `start` again resumes where it stopped.

## Flow

1. **Questions (one round, AskUserQuestion, grounded in the session).** Before asking, read what the session already knows (tickets, reports, files) and offer those answers as the recommended option. Only ask what changes the graph:
   - the goal and what is out of scope;
   - the tasks and their order (propose the list; the user cuts or edits);
   - for each task: **this session or an agent** (an agent only when the task is independent and fits in one prompt);
   - **may it commit, push or open PRs?** Default: no;
   - how to check each task (a command that exits 0 when it is done).
2. **Write the spec** in the format below and show a 3 to 6 line summary. Continue only after the user says ok.
3. **Start:** `graph.py start <spec> ${CLAUDE_SESSION_ID}`. Create the TUI tasks with exactly the subjects the command prints (`[id] title`) and begin with the current task.
4. **Move:** when a task is finished, mark its TUI task `completed`. The hook runs `done_when`; if it fails, the task stays open with the error and you fix it. If it passes, the next task's spec arrives with the tool result.
5. **Stop** only with a line `WAITING:` (CI, build, deploy), `DECISION:` (question for the user) or `BLOCKED:` (a real blocker). Stopping without one makes the hook hand the task back (at most 3 times per task).
6. **Decided something on your own?** Record it right away with `graph.py decide`: any choice the user did not explicitly ask for (reading of a requirement, a value you picked, an old test you changed, a rule you relaxed).
7. **End or BLOCKED:** run `graph.py report` and show the task table, the recorded decisions and what is still open, plus 1 to 3 lines of summary. Never say everything is solved while the table has an open task.
8. **Reality changed?** Edit the spec (tasks, dependencies, checks) and run `start` again. To turn it off: `graph.py stop`.

## Spec format

```json
{
  "name": "checkout-refactor",
  "goal": "one sentence with the outcome",
  "cwd": "~/code/my-app",
  "commit": false,
  "rules": ["rules that apply to every task"],
  "tasks": [
    {
      "id": "t1",
      "title": "short imperative title",
      "goal": "what is true when it is done",
      "spec": "details, files, real cases, what not to do",
      "done_when": ["a command that exits 0 when it is done"],
      "depends_on": [],
      "mode": "session"
    }
  ]
}
```

- JSON, not Markdown: models rewrite Markdown easily; here only the state file changes while it runs.
- `done_when` is a command, not an opinion. If no command fits, describe the manual check in `goal` and leave the list empty.
- `mode: "agent"` delegates the task to ONE agent with the task block; the hook still does the checking.
- `commit: false` makes the hook block `git commit`, `git push`, `gh pr create` and `gh pr merge` while the graph is active.

## Rules

- The hooks only act in the session that has an active graph; everywhere else they do nothing.
- Never edit `<slug>.state.json` by hand to "pass" a task.
- When the graph is done, report in a few lines and run `stop`.
