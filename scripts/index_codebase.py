#!/usr/bin/env python3
"""
vector-memory: Codebase indexer for Qdrant.

Indexes a workspace directory into a Qdrant vector collection for semantic search.

Configuration via environment variables:
  VECTOR_MEMORY_WORKSPACE    Path to index (default: current directory)
  VECTOR_MEMORY_COLLECTION   Qdrant collection name (default: basename of workspace)
  VECTOR_MEMORY_QDRANT_URL   Qdrant URL (default: http://localhost:6333)

Usage:
  python3 index_codebase.py                # Upsert all files
  python3 index_codebase.py --incremental  # Only reindex changed files
  python3 index_codebase.py --clean        # Drop collection and rebuild
  python3 index_codebase.py --dry-run      # Count files without indexing
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

# ── Configuration ────────────────────────────────────────────────────────────

_default_workspace = Path(os.environ.get("VECTOR_MEMORY_WORKSPACE", os.getcwd()))
WORKSPACE = _default_workspace.resolve()

_default_collection = WORKSPACE.name.lower().replace(" ", "-")
COLLECTION_NAME = os.environ.get("VECTOR_MEMORY_COLLECTION", _default_collection)

QDRANT_URL = os.environ.get("VECTOR_MEMORY_QDRANT_URL", "http://localhost:6333")
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
VECTOR_SIZE = 384

CHUNK_MIN = 40       # start looking for boundary after this many lines
CHUNK_MAX = 80       # hard split if no boundary found
CHUNK_FALLBACK = 60  # split point when no boundary found within window
CHUNK_OVERLAP = 5    # lines of overlap before boundary

STATE_FILE = Path(__file__).parent / ".qdrant-index-state.json"

INCLUDE_EXTENSIONS = {
    ".kt", ".java", ".xml", ".gradle", ".kts",
    ".ts", ".tsx", ".js", ".jsx", ".json",
    ".yaml", ".yml", ".properties", ".sql",
    ".py", ".go", ".rs", ".rb", ".md",
    ".sh", ".tf", ".toml",
}

EXCLUDE_DIRS = {
    "build", ".gradle", "node_modules", ".git",
    ".idea", "generated", "intermediates", "__pycache__",
    ".kotlin", "caches", ".venv", "venv", "dist", ".next",
    "target", ".terraform",
}

EXCLUDE_FILES = {
    ".qdrant-index-state.json",
}

# Regex patterns that indicate natural chunk boundaries (start of a new logical block)
BOUNDARY_PATTERNS = {
    "kt": [
        r"^\s*(fun |class |object |interface |override fun |@Composable|companion object)",
    ],
    "java": [
        r"^\s*(public |private |protected )?(static )?(class |interface |enum |void |[\w<>\[\]]+\s+\w+\s*\()",
    ],
    "ts": [
        r"^\s*(export |function |class |interface |const\s+\w+\s*=\s*\(|type\s+\w+)",
    ],
    "tsx": [
        r"^\s*(export |function |class |interface |const\s+\w+\s*=\s*\(|type\s+\w+)",
    ],
    "js": [
        r"^\s*(export |function |class |const\s+\w+\s*=\s*\(|module\.exports)",
    ],
    "py": [
        r"^\s*(def |class |async def )",
    ],
    "go": [
        r"^\s*(func |type )",
    ],
    "rs": [
        r"^\s*(fn |pub fn |struct |enum |impl |trait |mod )",
    ],
    "rb": [
        r"^\s*(def |class |module )",
    ],
    "xml": [
        r"^\s*<(?![\?!])\w+[\s>]",
    ],
    "yaml": [
        r"^---",
        r"^\w+:",
    ],
    "yml": [
        r"^---",
        r"^\w+:",
    ],
    "md": [
        r"^#{1,3}\s",
    ],
}


def _is_boundary(line: str, language: str) -> bool:
    """Check if a line matches a boundary pattern for the given language."""
    patterns = BOUNDARY_PATTERNS.get(language, [])
    for pat in patterns:
        if re.match(pat, line):
            return True
    return False


# ── Helpers ───────────────────────────────────────────────────────────────────

def should_index(path: Path) -> bool:
    if path.name in EXCLUDE_FILES:
        return False
    if path.suffix not in INCLUDE_EXTENSIONS:
        return False
    for part in path.parts:
        if part in EXCLUDE_DIRS:
            return False
    return True


def chunk_file(path: Path) -> list[dict]:
    """Split a file into chunks, preferring natural code boundaries."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    lines = text.splitlines()
    if not lines:
        return []

    language = path.suffix.lstrip(".")
    chunks = []
    start = 0

    while start < len(lines):
        end = min(start + CHUNK_FALLBACK, len(lines))

        if start + CHUNK_MIN < len(lines):
            boundary_found = False
            for candidate in range(start + CHUNK_MIN, min(start + CHUNK_MAX, len(lines))):
                if _is_boundary(lines[candidate], language):
                    end = candidate
                    boundary_found = True
                    break
            if not boundary_found:
                end = min(start + CHUNK_FALLBACK, len(lines))

        if end < len(lines) and (len(lines) - end) < 10:
            end = len(lines)

        chunk_text = "\n".join(lines[start:end])
        if chunk_text.strip():
            chunk_id = hashlib.sha256(
                f"{path}:{start}:{end}:{chunk_text}".encode()
            ).hexdigest()

            try:
                rel = path.relative_to(WORKSPACE)
                repo = rel.parts[0] if len(rel.parts) > 1 else path.name
            except ValueError:
                repo = path.name

            doc = f"File: {path}\nLines: {start+1}-{end}\n\n{chunk_text}"
            chunks.append({
                "id": chunk_id,
                "text": doc,
                "payload": {
                    "document": doc,
                    "file": str(path),
                    "repo": repo,
                    "language": language,
                    "line_start": start + 1,
                    "line_end": end,
                },
            })

        start = max(end - CHUNK_OVERLAP, start + 1) if end < len(lines) else end

    return chunks


def collect_files(workspace: Path) -> list[Path]:
    files = []
    for root, dirs, filenames in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fname in filenames:
            p = Path(root) / fname
            if should_index(p):
                files.append(p)
    return files


# ── Incremental support ──────────────────────────────────────────────────────

def find_git_repos(workspace: Path) -> list[Path]:
    """Find all git repositories under the workspace."""
    repos = []
    for root, dirs, _ in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        if ".git" in os.listdir(root):
            repos.append(Path(root))
            dirs.clear()
    return repos


def get_git_head(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo, capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def get_git_changed_files(repo: Path, since_hash: str) -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMR", since_hash, "HEAD"],
            cwd=repo, capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []
        return [repo / f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def get_git_deleted_files(repo: Path, since_hash: str) -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=D", since_hash, "HEAD"],
            cwd=repo, capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []
        return [repo / f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_indexed": None, "git_repos": {}, "file_mtimes": {}}


def save_state(state: dict):
    state["last_indexed"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2))


def collect_incremental_changes(workspace: Path, state: dict) -> tuple[list[Path], list[Path]]:
    """Return (changed_files, deleted_files) since last index."""
    changed = []
    deleted = []

    git_repos = find_git_repos(workspace)
    git_covered_dirs = set()

    for repo in git_repos:
        rel = str(repo.relative_to(workspace))
        git_covered_dirs.add(repo)
        stored_hash = state["git_repos"].get(rel)
        current_hash = get_git_head(repo)

        if stored_hash and current_hash and stored_hash != current_hash:
            repo_changed = get_git_changed_files(repo, stored_hash)
            repo_deleted = get_git_deleted_files(repo, stored_hash)
            changed.extend(f for f in repo_changed if should_index(f))
            deleted.extend(f for f in repo_deleted if should_index(f))
        elif not stored_hash:
            for f in collect_files(repo):
                changed.append(f)

        if current_hash:
            state["git_repos"][rel] = current_hash

    stored_mtimes = state.get("file_mtimes", {})
    new_mtimes = {}

    for root, dirs, filenames in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        root_path = Path(root)

        if any(root_path == repo or str(root_path).startswith(str(repo) + os.sep)
               for repo in git_covered_dirs):
            continue

        for fname in filenames:
            p = root_path / fname
            if not should_index(p):
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue

            file_key = str(p)
            new_mtimes[file_key] = mtime
            stored_mtime = stored_mtimes.get(file_key)
            if stored_mtime is None or mtime > stored_mtime:
                changed.append(p)

    for file_key in stored_mtimes:
        if file_key not in new_mtimes and not any(
            file_key.startswith(str(repo)) for repo in git_covered_dirs
        ):
            deleted.append(Path(file_key))

    state["file_mtimes"] = new_mtimes
    return changed, deleted


# ── Embed and upsert ─────────────────────────────────────────────────────────

def _embed_and_upsert(client: QdrantClient, embedder, all_chunks: list[dict]):
    batch_size = 100
    texts = [c["text"] for c in all_chunks]
    total = len(all_chunks)
    indexed = 0

    print(f"Indexing {total} chunks in batches of {batch_size}...")
    for i in range(0, total, batch_size):
        batch_chunks = all_chunks[i : i + batch_size]
        batch_texts = texts[i : i + batch_size]
        embeddings = list(embedder.embed(batch_texts))

        points = [
            PointStruct(
                id=int(c["id"][:8], 16),
                vector={"fast-all-minilm-l6-v2": list(emb)},
                payload=c["payload"],
            )
            for c, emb in zip(batch_chunks, embeddings)
        ]

        client.upsert(collection_name=COLLECTION_NAME, points=points)
        indexed += len(points)
        print(f"  {indexed}/{total} ({indexed/total*100:.0f}%)", end="\r")

    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Index a codebase into Qdrant for semantic search")
    parser.add_argument("--clean", action="store_true", help="Drop and rebuild collection")
    parser.add_argument("--dry-run", action="store_true", help="Count files without indexing")
    parser.add_argument("--incremental", action="store_true",
                        help="Only reindex files changed since last run")
    args = parser.parse_args()

    print(f"Workspace:  {WORKSPACE}")
    print(f"Collection: {COLLECTION_NAME}")
    print(f"Qdrant:     {QDRANT_URL}")
    print()

    client = QdrantClient(url=QDRANT_URL)

    try:
        client.get_collections()
    except Exception as e:
        print(f"ERROR: Cannot connect to Qdrant at {QDRANT_URL}")
        print(f"  Make sure Qdrant is running: docker compose -f docker/docker-compose.yml up -d")
        print(f"  Details: {e}")
        sys.exit(1)

    if args.clean:
        collections = [c.name for c in client.get_collections().collections]
        if COLLECTION_NAME in collections:
            print(f"Dropping collection '{COLLECTION_NAME}'...")
            client.delete_collection(COLLECTION_NAME)

    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME not in collections:
        print(f"Creating collection '{COLLECTION_NAME}'...")
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config={"fast-all-minilm-l6-v2": VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE)},
        )

    # Incremental mode
    if args.incremental:
        state = load_state()
        print(f"Scanning {WORKSPACE} for changes...")
        changed, deleted_files = collect_incremental_changes(WORKSPACE, state)

        if not changed and not deleted_files:
            print("Index up to date. No changes detected.")
            save_state(state)
            return

        print(f"Found {len(changed)} changed files, {len(deleted_files)} deleted files")

        if args.dry_run:
            for f in changed[:20]:
                print(f"  changed: {f}")
            for f in deleted_files[:10]:
                print(f"  deleted: {f}")
            return

        if deleted_files:
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            for df in deleted_files:
                client.delete(
                    collection_name=COLLECTION_NAME,
                    points_selector=Filter(
                        must=[FieldCondition(key="file", match=MatchValue(value=str(df)))]
                    ),
                )
            print(f"Removed chunks for {len(deleted_files)} deleted files")

        if changed:
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            for cf in changed:
                client.delete(
                    collection_name=COLLECTION_NAME,
                    points_selector=Filter(
                        must=[FieldCondition(key="file", match=MatchValue(value=str(cf)))]
                    ),
                )

        all_chunks = []
        for f in changed:
            if f.exists():
                all_chunks.extend(chunk_file(f))
        print(f"Generated {len(all_chunks)} chunks from changed files")

        if all_chunks:
            from fastembed import TextEmbedding
            embedder = TextEmbedding(model_name=EMBEDDING_MODEL)
            _embed_and_upsert(client, embedder, all_chunks)

        save_state(state)
        print(f"Done. Reindexed {len(changed)} files ({len(all_chunks)} chunks).")
        return

    # Full mode
    print(f"Scanning {WORKSPACE}...")
    files = collect_files(WORKSPACE)
    print(f"Found {len(files)} indexable files")

    if args.dry_run:
        for f in files[:20]:
            try:
                print(f"  {f.relative_to(WORKSPACE)}")
            except ValueError:
                print(f"  {f}")
        if len(files) > 20:
            print(f"  ... and {len(files) - 20} more")
        return

    print("Chunking files...")
    all_chunks = []
    for f in files:
        all_chunks.extend(chunk_file(f))
    print(f"Generated {len(all_chunks)} chunks")

    from fastembed import TextEmbedding
    print(f"Loading embedding model '{EMBEDDING_MODEL}' (first run downloads ~90MB)...")
    embedder = TextEmbedding(model_name=EMBEDDING_MODEL)

    _embed_and_upsert(client, embedder, all_chunks)
    print(f"\nDone. {len(all_chunks)} chunks indexed into '{COLLECTION_NAME}'.")
    print(f"\nTo search: use the vector-memory skill in Claude Code and ask codebase questions.")


if __name__ == "__main__":
    main()
