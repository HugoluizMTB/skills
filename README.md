# skills

Claude Code skills I use every day.

## graph

Runs a long job as a graph of tasks written in a spec, one task at a time:

- `/graph` asks a few questions using what the session already knows, writes the spec as JSON and creates the tasks in the TUI.
- Only the current task's spec is injected. The rest is a list of titles until it is their turn.
- Marking a task completed runs its `done_when` commands in a `TaskCompleted` hook. A failure keeps the task open and sends the output back. A pass injects the next task's spec through a `PostToolUse` hook on `TaskUpdate`.
- A `Stop` hook hands the current task back when the agent stops without saying why. It may stop with `WAITING:` (CI, build, deploy), `DECISION:` (a question for you) or `BLOCKED:`.
- With `commit: false`, a `PreToolUse` hook blocks `git commit`, `git push`, `gh pr create` and `gh pr merge` while the graph is active.
- Each task can run in the session or be delegated to one agent.
- `graph.py decide` records every choice the agent made without being asked, and `graph.py report` prints a table of the tasks, those decisions and what is still open, at the end or when the graph is blocked.

### Flow

```mermaid
flowchart TD
    A["/graph"] --> B["Questions grounded in the session<br/>goal, tasks, session or agent, commit?, checks"]
    B --> C["Write spec.json<br/>user says ok"]
    C --> D["graph.py start<br/>create TUI tasks, inject the first task only"]
    D --> E["Work on the current task"]
    E --> F{"Agent stops?"}
    F -- "no line WAITING / DECISION / BLOCKED" --> G["Stop hook hands the task back<br/>max 3 per task"]
    G --> E
    F -- "WAITING / DECISION / BLOCKED" --> H["Stop allowed<br/>graph.py report if BLOCKED"]
    E --> I["Mark TUI task completed"]
    I --> J{"TaskCompleted hook<br/>runs done_when"}
    J -- "fails" --> K["Task stays open<br/>error goes back to the agent"]
    K --> E
    J -- "passes" --> L["PostToolUse hook on TaskUpdate<br/>saves state, injects next ready task"]
    L --> M{"Tasks left?"}
    M -- "yes" --> E
    M -- "no" --> N["graph.py report<br/>task table, decisions, what is open"]
```

### One task, hook by hook

```mermaid
sequenceDiagram
    participant Agent
    participant Hooks as graph.py hooks
    participant State as spec.state.json
    Agent->>Hooks: Bash git commit (commit false)
    Hooks-->>Agent: blocked, keep it in the working tree
    Agent->>Hooks: graph.py decide "chose X"
    Hooks->>State: decision recorded
    Agent->>Hooks: TaskUpdate status completed
    Hooks->>Hooks: TaskCompleted runs done_when
    alt check fails
        Hooks-->>Agent: exit 2 with the failing output
    else check passes
        Hooks->>State: task done, checkpoint
        Hooks-->>Agent: additionalContext with the next task spec
    end
    Agent->>Hooks: Stop without WAITING / DECISION / BLOCKED
    Hooks-->>Agent: decision block, continue the current task
```

The state file next to the spec is the checkpoint, so `start` resumes where it stopped. The ideas come from LangGraph (state, nodes, edges, checkpoints, interrupts) and from the harness engineering write-ups by Anthropic, OpenAI and Mitchell Hashimoto.

### Install

```bash
git clone https://github.com/HugoluizMTB/skills.git
mkdir -p ~/.claude/skills && cp -r skills/graph ~/.claude/skills/
```

Needs Python 3 and a Claude Code version with skill hooks and the `TaskCompleted` hook event.
