"""Task context from seekdb, with restartable task state in PowerContext."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from contextlib import closing
from pathlib import Path

import pylibseekdb
import pymysql
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    RememberMemoryRequest,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft


def _paths(data_dir: Path) -> tuple[Path, Path, Path]:
    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "seekdb.db", data_dir / "powercontext.db", data_dir / "scope.json"


def _documents(project: Path, task: str) -> tuple[str, list[dict[str, str]]]:
    project = project.resolve()
    manifest = json.loads((project / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["tasks"][task]
    documents = []
    for source in entry["sources"]:
        path = (project / source["path"]).resolve()
        if not path.is_relative_to(project) or not path.is_file():
            raise ValueError(f"Invalid source path: {source['path']}")
        content = path.read_text(encoding="utf-8")
        documents.append({
            "task": task,
            "kind": source["kind"],
            "path": source["path"],
            "content": content,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        })
    return entry["summary"], documents


def _seekdb_import(data_dir: Path, documents: list[dict[str, str]]) -> list[dict[str, str]]:
    seekdb_path, _, _ = _paths(data_dir)
    with closing(pylibseekdb.open(str(seekdb_path))) as db:
        with pymysql.connect(database="test", autocommit=True,
                             cursorclass=pymysql.cursors.DictCursor,
                             **db.connection_options()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("""CREATE TABLE IF NOT EXISTS engineering_docs (
                    task_key VARCHAR(80) NOT NULL,
                    path VARCHAR(255) NOT NULL,
                    kind VARCHAR(32) NOT NULL,
                    sha256 CHAR(64) NOT NULL,
                    content LONGTEXT NOT NULL,
                    PRIMARY KEY (task_key, path))""")
                for item in documents:
                    cursor.execute("""INSERT INTO engineering_docs
                        (task_key, path, kind, sha256, content) VALUES (%s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE kind=VALUES(kind),
                            sha256=VALUES(sha256), content=VALUES(content)""",
                        (item["task"], item["path"], item["kind"],
                         item["sha256"], item["content"]))
    return [{key: item[key] for key in ("task", "path", "kind", "sha256")} for item in documents]


def _seekdb_query(data_dir: Path, task: str) -> list[dict[str, str]]:
    seekdb_path, _, _ = _paths(data_dir)
    with closing(pylibseekdb.open(str(seekdb_path))) as db:
        with pymysql.connect(database="test", autocommit=True,
                             cursorclass=pymysql.cursors.DictCursor,
                             **db.connection_options()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("""SELECT path, kind, sha256, content
                    FROM engineering_docs WHERE task_key = %s ORDER BY path""", (task,))
                rows = list(cursor.fetchall())
    if not rows:
        raise ValueError(f"No imported sources for task {task!r}")
    rank = {"spec": 0, "code": 1, "test": 2}
    return sorted(rows, key=lambda item: (rank.get(item["kind"], 9), item["path"]))


def _runtime_config(data_dir: Path) -> BuiltinConfig:
    _, runtime_path, _ = _paths(data_dir)
    return BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{runtime_path.as_posix()}"))


async def _scope_id(data_dir: Path, runtime) -> str:
    _, _, scope_file = _paths(data_dir)
    if scope_file.exists():
        return json.loads(scope_file.read_text(encoding="utf-8"))["scope_id"]
    if runtime.scopes is None:
        raise RuntimeError("PowerContext Scope registry is unavailable")
    scope = await runtime.scopes.create(ScopeDraft(
        title="MiniMax SDD development task",
        summary="Persistent task progress and verification records",
        idempotency_key="minimax-sdd-example",
    ))
    scope_file.write_text(json.dumps({"scope_id": scope.scope_id}), encoding="utf-8")
    return scope.scope_id


async def _read_state(data_dir: Path, task: str) -> list[str]:
    async with open_builtin_runtime(_runtime_config(data_dir)) as runtime:
        scope = await _scope_id(data_dir, runtime)
        result = await runtime.memory.for_scope(scope).search(
            SearchMemoryRequest(query=f"task {task} progress verification", mode="fts", limit=10)
        )
        return [hit.text for hit in result.hits if f"task {task} " in hit.text]


async def _save_state(data_dir: Path, task: str, done: str, next_step: str, verification: str) -> dict:
    async with open_builtin_runtime(_runtime_config(data_dir)) as runtime:
        scope = await _scope_id(data_dir, runtime)
        text = (f"task {task} progress\nCompleted: {done}\n"
                f"Next: {next_step}\nVerification: {verification}")
        result = await runtime.memory.for_scope(scope).remember(
            RememberMemoryRequest(entries=(MemoryEntryInput(kind="outcome", text=text),))
        )
        return {"scope_id": scope, "memory_ref": result.memory_ref.model_dump(mode="json"),
                "saved_text": text}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("import", "context", "record"))
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--project", type=Path, default=Path(__file__).parent / "project")
    parser.add_argument("--done", default="")
    parser.add_argument("--next", dest="next_step", default="")
    parser.add_argument("--verification", default="")
    args = parser.parse_args()
    if args.command == "import":
        summary, documents = _documents(args.project, args.task)
        output = {"task": args.task, "summary": summary,
                  "imported": _seekdb_import(args.data, documents)}
    elif args.command == "context":
        summary, _ = _documents(args.project, args.task)
        output = {"task": args.task, "summary": summary,
                  "database_query": "SELECT ... FROM engineering_docs WHERE task_key = ?",
                  "sources": _seekdb_query(args.data, args.task),
                  "previous_state": asyncio.run(_read_state(args.data, args.task))}
    else:
        if not args.done or not args.next_step or not args.verification:
            parser.error("record requires --done, --next, and --verification")
        output = asyncio.run(_save_state(args.data, args.task, args.done,
                                         args.next_step, args.verification))
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

