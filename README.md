# vector-memory

> Semantic codebase search + persistent session memory for Claude Code — powered by Qdrant.

Give Claude Code a long-term memory. It searches your codebase by meaning (not just keywords) and remembers decisions, fixes, and insights across sessions.

---

## How It Works

```
┌─────────────────────────────────────────────────────┐
│                  Claude Code Session                │
│                                                     │
│  ┌──────────────┐  MCP tools  ┌─────────────────┐   │
│  │  qdrant-mcp  │◄───────────►│  Qdrant Docker  │   │
│  │  server      │             │  :6333          │   │
│  └──────────────┘             └─────────────────┘   │
│                                     ▲               │
│                          index_codebase.py          │
│                          (embeds your source files) │
└─────────────────────────────────────────────────────┘
```

- **`qdrant-find`** — semantic search across your entire codebase. Ask "where is authentication handled?" and get exact file + line references.
- **`qdrant-store`** — save decisions, bug fixes, and insights. Retrieved in future sessions automatically.
- **`vector-memory` skill** — teaches Claude when to search and what to store.
- **Smart chunking** — splits code at function/class boundaries instead of arbitrary line counts.
- **Incremental indexing** — only re-embeds files that changed (git diff for repos, mtime for non-git dirs).
- **Session hooks** — auto-starts Qdrant and reindexes on every session start.

---

## Requirements

- [Docker](https://docs.docker.com/get-docker/) (any runtime: Docker Desktop, Rancher, OrbStack, Colima, Linux)
- [Claude Code](https://claude.ai/code)
- Python 3.11+
- `uv` (auto-installed by `install.sh` if missing)

---

## Quickstart

```bash
# 1. Clone and install
git clone https://github.com/skyner-group/vector-memory
cd vector-memory
./install.sh

# 2. Add MCP config to your project
cp templates/.mcp.json.template /path/to/your/project/.mcp.json
# Edit .mcp.json and set COLLECTION_NAME to your project name (e.g. "my-app")

# 3. Index your codebase
cd scripts
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
VECTOR_MEMORY_WORKSPACE=/path/to/your/project python3 index_codebase.py

# 4. Restart Claude Code and run: /vector-memory
```

---

## Usage

### Activate the skill
```
/vector-memory
```
Claude will check Qdrant is running, read your collection name, and apply search-first rules.

### Search the codebase
Just ask naturally — Claude searches automatically:
> "Where is payment routing handled?"
> "Find the authentication middleware"
> "How does the event bus work?"

### Store a note
> "Remember that we fixed the BUSY state bug by sending a broadcast from finishWithError()"

### Manual search/store
```
Use qdrant-find to search for "database connection pooling"
Use qdrant-store to remember that X is handled in Y
```

---

## Always-On Mode (recommended)

Instead of running `/vector-memory` each session, add instructions to your project's `CLAUDE.md`:

```markdown
## Vector Memory (Qdrant)

This workspace uses Qdrant vector search (`my-project` collection) for codebase knowledge.

### Search Before Answering
Before answering any question about the codebase, use `qdrant-find` first.
After getting results, use the Read tool on the returned file paths.

### Store After Solving
After fixing a bug, making a decision, or discovering how something works, use `qdrant-store`:
[DATE] <summary> | Root cause: ... | Fix: ... | Key files: ...
```

This makes vector memory active every session without manual invocation.

---

## Automatic Session Hooks (recommended)

Auto-start Qdrant and reindex changed files on every session:

```bash
# 1. Copy and configure the reindex script
cp templates/session-reindex.sh.template /path/to/your/project/scripts/session-reindex.sh
# Edit the VECTOR_MEMORY_WORKSPACE and VECTOR_MEMORY_SCRIPTS paths
chmod +x /path/to/your/project/scripts/session-reindex.sh

# 2. Set up the hook
mkdir -p /path/to/your/project/.claude
cp templates/hooks.json.template /path/to/your/project/.claude/hooks.json
# Edit the command path to point to your session-reindex.sh
```

Now every Claude Code session automatically ensures Qdrant is running and your index is fresh.

---

## Indexing Modes

### Full index (default)
Embeds all indexable files:
```bash
VECTOR_MEMORY_WORKSPACE=/path/to/project python3 index_codebase.py
```

### Incremental index
Only re-embeds files that changed since last run:
```bash
VECTOR_MEMORY_WORKSPACE=/path/to/project python3 index_codebase.py --incremental
```

Uses hybrid change detection:
- **Git repos** — `git diff` against stored commit hash
- **Non-git directories** — file modification time comparison
- **Mixed workspaces** — auto-detects which directories are git repos

### Clean rebuild
Drops the collection and rebuilds from scratch:
```bash
VECTOR_MEMORY_WORKSPACE=/path/to/project python3 index_codebase.py --clean
```

### Dry run
Count files without indexing:
```bash
VECTOR_MEMORY_WORKSPACE=/path/to/project python3 index_codebase.py --dry-run
```

---

## Smart Chunking

The indexer splits code at natural boundaries instead of fixed line counts:

| Language | Boundary patterns |
|----------|------------------|
| Kotlin/Java | `fun`, `class`, `object`, `interface`, `@Composable` |
| TypeScript/JS | `export`, `function`, `class`, `interface`, arrow functions |
| Python | `def`, `class`, `async def` |
| Go | `func`, `type` |
| Rust | `fn`, `struct`, `enum`, `impl`, `trait` |
| Ruby | `def`, `class`, `module` |
| XML | Opening tags |
| YAML | `---`, top-level keys |
| Markdown | Headings |

Chunks target 40-80 lines, splitting at the nearest boundary. Tiny trailing fragments (< 10 lines) are merged with the previous chunk.

---

## Manual Setup

If you prefer to understand each step:

**1. Start Qdrant**
```bash
docker compose -f docker/docker-compose.yml up -d
```

**2. Install uv** (if not already installed)
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**3. Install the Claude Code skill**
```bash
mkdir -p ~/.claude/skills/vector-memory
cp skill/SKILL.md ~/.claude/skills/vector-memory/SKILL.md
```

**4. Configure your project**
```bash
cp templates/.mcp.json.template /path/to/your/project/.mcp.json
# Edit and set COLLECTION_NAME
```

**5. Index your codebase**
```bash
cd scripts
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
VECTOR_MEMORY_WORKSPACE=/path/to/project python3 index_codebase.py
```

**6. Restart Claude Code**

---

## Configuration

All indexer settings via environment variables:

| Variable | Default | Description |
|---|---|---|
| `VECTOR_MEMORY_WORKSPACE` | current directory | Path to index |
| `VECTOR_MEMORY_COLLECTION` | workspace directory name | Qdrant collection name |
| `VECTOR_MEMORY_QDRANT_URL` | `http://localhost:6333` | Qdrant URL |

---

## File Types Indexed

`.kt` `.java` `.ts` `.tsx` `.js` `.jsx` `.py` `.go` `.rs` `.rb` `.sql` `.md` `.yaml` `.yml` `.json` `.properties` `.xml` `.gradle` `.sh` `.tf` `.toml`

Excluded: `build/`, `node_modules/`, `.git/`, `.venv/`, `dist/`, `target/`

---

## License

MIT — see [LICENSE](LICENSE)
