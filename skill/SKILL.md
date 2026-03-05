---
name: vector-memory
description: Initialises Qdrant vector DB and teaches Claude to proactively search the codebase and store knowledge using qdrant-find and qdrant-store MCP tools
triggers: vector-memory, search codebase, remember this, store this, what do you know about, qdrant
---

# Vector Memory

## On Invoke — Run These Steps First

When this skill is invoked, execute the following initialisation before anything else:

**Step 1: Check and start Qdrant**

Run:
```bash
docker ps --filter "name=qdrant" --format "{{.Names}}"
```

- If output is `qdrant` → already running, proceed
- If output is empty → run `docker start qdrant` and wait 3 seconds
- If docker start fails → tell the user to run: `docker compose -f path/to/vector-memory/docker/docker-compose.yml up -d`

**Step 2: Read the collection name for this workspace**

Run:
```bash
python3 -c "
import json, os
mcp = os.path.join(os.getcwd(), '.mcp.json')
if os.path.exists(mcp):
    d = json.load(open(mcp))
    print(d['mcpServers']['qdrant']['env']['COLLECTION_NAME'])
else:
    print('NO_MCP_JSON')
"
```

Use the printed value as `COLLECTION_NAME` for all qdrant tool calls this session.
If it prints `NO_MCP_JSON`, warn the user: this workspace has no `.mcp.json` — copy `templates/.mcp.json.template` to your project root and fill in `COLLECTION_NAME`.

---

## How to Use qdrant-find

**Use `qdrant-find` BEFORE answering any question about the codebase.**

Trigger phrases that should always invoke a search first:
- "where is X handled / implemented / defined"
- "how does Y work"
- "find the file / class / method that does Z"
- "show me the code for..."
- "what handles / processes / manages..."

**Example queries:**
- `qdrant-find("payment routing logic")`
- `qdrant-find("user authentication flow")`
- `qdrant-find("database connection setup")`
- `qdrant-find("error handling middleware")`

**After getting results:** Read the returned file paths and line numbers. Use the Read tool on those specific locations rather than searching blindly.

---

## How to Use qdrant-store

**Use `qdrant-store` after any significant discovery, fix, or decision.**

Store when you:
- Fix a bug → store the root cause and fix
- Make an architectural decision → store what was decided and why
- Discover how something works → store the insight
- Solve a non-obvious problem → store the solution

**Format for stored notes:**
```
[DATE] <one-line summary>

Root cause / context: <what was happening>
Fix / decision: <what was done>
Key files: <file:line references>
```

---

## Rules for This Session

1. **Search before answering** — never guess at file locations; always `qdrant-find` first
2. **Store after solving** — if you spent more than 2 minutes on something, store the insight
3. **Cite sources** — when answering from search results, mention the file and line range
4. **Don't re-search** — if you already searched for something this session, reuse the result

---

## Always-On Mode (recommended)

Instead of invoking `/vector-memory` each session, add these instructions to your project's `CLAUDE.md`:

```markdown
## Vector Memory (Qdrant)

This workspace uses Qdrant vector search (`<collection-name>` collection) for codebase knowledge.

### Search Before Answering
Before answering any question about the codebase, use `qdrant-find` first.
After getting results, use the Read tool on the returned file paths.

### Store After Solving
After fixing a bug, making a decision, or discovering how something works, use `qdrant-store`:
[DATE] <summary> | Root cause: ... | Fix: ... | Key files: ...
```

This makes vector memory active every session without manual skill invocation.

---

## Automatic Session Hooks (recommended)

Set up a SessionStart hook to automatically start Qdrant and reindex changed files:

1. Copy `templates/session-reindex.sh.template` to your project's scripts directory
2. Edit the paths at the top of the script
3. Make it executable: `chmod +x session-reindex.sh`
4. Copy `templates/hooks.json.template` to your project's `.claude/hooks.json`
5. Edit the command path to point to your script

This ensures every session starts with Qdrant running and a fresh index.

---

## Re-indexing (when needed)

After significant code changes:
```bash
cd path/to/vector-memory/scripts
source .venv/bin/activate

# Incremental (only changed files — fast)
VECTOR_MEMORY_WORKSPACE=/path/to/your/project python3 index_codebase.py --incremental

# Full rebuild (after large refactors)
VECTOR_MEMORY_WORKSPACE=/path/to/your/project python3 index_codebase.py --clean
```
