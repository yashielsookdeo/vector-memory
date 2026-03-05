#!/usr/bin/env bash
# vector-memory installer
# Sets up Qdrant vector DB and installs the Claude Code skill.
# Usage: ./install.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="${HOME}/.claude/skills/vector-memory"

# ── Colors ───────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'
else
  GREEN=''; YELLOW=''; RED=''; BLUE=''; NC=''
fi

ok()   { echo -e "${GREEN}✓${NC} $*"; }
info() { echo -e "${BLUE}→${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*"; exit 1; }

echo ""
echo "  vector-memory installer"
echo "  ─────────────────────────────────────────"
echo ""

# ── Step 1: Check Docker ──────────────────────────────────────────────────────
info "Checking Docker..."
if ! command -v docker &>/dev/null; then
  fail "Docker not found. Install from https://docs.docker.com/get-docker/ and try again."
fi
if ! docker info &>/dev/null 2>&1; then
  fail "Docker is installed but not running. Start Docker and try again."
fi
ok "Docker is available"

# ── Step 2: Check/install uv ──────────────────────────────────────────────────
info "Checking uv..."
if ! command -v uv &>/dev/null; then
  warn "uv not found. Installing..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
  if ! command -v uv &>/dev/null; then
    fail "uv install failed. Install manually: https://docs.astral.sh/uv/getting-started/installation/"
  fi
fi
ok "uv $(uv --version | cut -d' ' -f2) is available"

# ── Step 3: Start Qdrant ──────────────────────────────────────────────────────
info "Starting Qdrant..."
if docker ps --filter "name=qdrant" --format "{{.Names}}" | grep -q "^qdrant$"; then
  ok "Qdrant is already running"
else
  mkdir -p "${HOME}/.qdrant"
  docker compose -f "${REPO_DIR}/docker/docker-compose.yml" up -d
  info "Waiting for Qdrant to be ready..."
  for i in $(seq 1 20); do
    if curl -sf http://localhost:6333/healthz &>/dev/null; then
      break
    fi
    sleep 1
  done
  if ! curl -sf http://localhost:6333/healthz &>/dev/null; then
    fail "Qdrant did not start in time. Check: docker logs qdrant"
  fi
  ok "Qdrant is running at http://localhost:6333"
fi

# ── Step 4: Install Claude Code skill ────────────────────────────────────────
info "Installing vector-memory skill..."
mkdir -p "${SKILL_DIR}"
cp "${REPO_DIR}/skill/SKILL.md" "${SKILL_DIR}/SKILL.md"
ok "Skill installed at ${SKILL_DIR}/SKILL.md"

# ── Step 5: Set up Python venv for indexer ───────────────────────────────────
info "Setting up indexer dependencies..."
if [ ! -d "${REPO_DIR}/scripts/.venv" ]; then
  python3 -m venv "${REPO_DIR}/scripts/.venv"
  source "${REPO_DIR}/scripts/.venv/bin/activate"
  pip install -q -r "${REPO_DIR}/scripts/requirements.txt"
  ok "Indexer dependencies installed"
else
  ok "Indexer venv already exists"
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}Setup complete!${NC} Next steps:"
echo ""
echo "  1. Copy the MCP config to your project:"
echo "     cp ${REPO_DIR}/templates/.mcp.json.template /path/to/your/project/.mcp.json"
echo "     Edit .mcp.json and set COLLECTION_NAME to your project name"
echo ""
echo "  2. Index your codebase:"
echo "     cd ${REPO_DIR}/scripts && source .venv/bin/activate"
echo "     VECTOR_MEMORY_WORKSPACE=/path/to/your/project python3 index_codebase.py"
echo ""
echo "  3. (Optional) Set up always-on mode:"
echo "     Add vector memory instructions to your project's CLAUDE.md"
echo "     See: ${REPO_DIR}/README.md#always-on-mode-recommended"
echo ""
echo "  4. (Optional) Set up session hooks for auto-reindex:"
echo "     cp ${REPO_DIR}/templates/session-reindex.sh.template /path/to/your/project/scripts/session-reindex.sh"
echo "     cp ${REPO_DIR}/templates/hooks.json.template /path/to/your/project/.claude/hooks.json"
echo "     Edit paths in both files, then chmod +x the script"
echo "     See: ${REPO_DIR}/README.md#automatic-session-hooks-recommended"
echo ""
echo "  5. Restart Claude Code and run: /vector-memory"
echo ""
