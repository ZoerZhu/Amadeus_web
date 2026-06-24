from __future__ import annotations

import argparse
import asyncio
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = Path(r"E:\Amadeus\Amadeus_web")
DEFAULT_TARGET_ROOT = ROOT_DIR
DEFAULT_SOURCE_DB = DEFAULT_SOURCE_ROOT / "agent_state" / "amadeus_web.sqlite3"
DEFAULT_TARGET_DB = DEFAULT_TARGET_ROOT / "agent_state" / "amadeus.sqlite3"

MIGRATED_TABLES = [
    "app_settings",
    "conversations",
    "chat_messages",
    "projects",
    "conversation_projects",
    "memory_nodes",
    "memory_edges",
    "memory_embedding_meta",
    "memory_jobs",
    "agent_tasks",
    "agent_task_events",
    "agent_artifacts",
    "agent_file_snapshots",
    "agent_permission_requests",
    "mcp_servers",
    "skill_packages",
]

DELETE_ORDER = [
    "agent_permission_requests",
    "agent_file_snapshots",
    "agent_artifacts",
    "agent_task_events",
    "agent_tasks",
    "memory_jobs",
    "memory_embedding_meta",
    "memory_edges",
    "memory_nodes",
    "conversation_projects",
    "projects",
    "chat_messages",
    "conversations",
    "mcp_servers",
    "skill_packages",
    "app_settings",
]


def qident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def db_uri(path: Path, *, readonly: bool = False) -> str:
    suffix = "?mode=ro" if readonly else ""
    return f"file:{path.resolve().as_posix()}{suffix}"


def table_names(conn: sqlite3.Connection, schema: str = "main") -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            f"SELECT name FROM {schema}.sqlite_master WHERE type IN ('table','view')"
        ).fetchall()
    }


def table_columns(conn: sqlite3.Connection, table: str, schema: str = "main") -> list[str]:
    pragma = f"PRAGMA {schema}.table_info({qident(table)})"
    return [row[1] for row in conn.execute(pragma).fetchall()]


def table_count(conn: sqlite3.Connection, table: str, schema: str = "main") -> int | str:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {schema}.{qident(table)}").fetchone()[0])
    except Exception as error:
        return f"ERR:{error}"


def print_counts(conn: sqlite3.Connection, tables: Iterable[str], schema: str = "main") -> None:
    names = table_names(conn, schema)
    for table in tables:
        if table not in names:
            print(f"  {table}: missing")
            continue
        print(f"  {table}: {table_count(conn, table, schema)}")


def backup_target(db_path: Path) -> None:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(db_path) + suffix)
        if not path.exists():
            continue
        backup = path.with_name(f"{path.name}.bak-{timestamp}")
        shutil.copy2(path, backup)
        print(f"backup: {path} -> {backup}")


async def ensure_target_schema(target_db: Path) -> None:
    sys.path.insert(0, str(ROOT_DIR))
    from backend.amadeus_app.storage import SQLiteStorage

    storage = SQLiteStorage(target_db)
    await storage.connect()
    await storage.close()


def copy_skill_files(source_root: Path, target_root: Path) -> None:
    source_skills = source_root / "agent_state" / "skills"
    target_skills = target_root / "agent_state" / "skills"
    if not source_skills.exists():
        print(f"skills: source missing, skipped: {source_skills}")
        return
    target_skills.mkdir(parents=True, exist_ok=True)
    for item in source_skills.iterdir():
        if not item.is_dir():
            continue
        shutil.copytree(item, target_skills / item.name, dirs_exist_ok=True)
    print(f"skills: copied {source_skills} -> {target_skills}")


def update_skill_paths(conn: sqlite3.Connection, source_root: Path, target_root: Path) -> None:
    old_prefix = str((source_root / "agent_state" / "skills").resolve())
    new_prefix = str((target_root / "agent_state" / "skills").resolve())
    rows = conn.execute("SELECT id, path FROM skill_packages").fetchall()
    for row in rows:
        skill_id, raw_path = row[0], str(row[1] or "")
        if not raw_path:
            continue
        path = Path(raw_path)
        if str(path).startswith(old_prefix):
            new_path = str(path).replace(old_prefix, new_prefix, 1)
        else:
            new_path = str((target_root / "agent_state" / "skills" / skill_id).resolve())
        conn.execute("UPDATE skill_packages SET path = ? WHERE id = ?", (new_path, skill_id))


def space_separate_cjk(text: str) -> str:
    import re

    if not text:
        return ""
    result = re.sub(r"([\u4e00-\u9fff])", r" \1 ", text)
    return re.sub(r"\s+", " ", result).strip()


def rebuild_memory_fts(conn: sqlite3.Connection) -> int:
    conn.execute("DELETE FROM memory_fts")
    rows = conn.execute(
        """
        SELECT id, node_type, path, label, summary, full_content, keywords
        FROM memory_nodes
        WHERE is_active = 1
        """
    ).fetchall()
    for row in rows:
        conn.execute(
            """
            INSERT INTO memory_fts
                (node_id, node_type, path, label, summary, full_content, keywords)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row[0],
                row[1],
                space_separate_cjk(row[2] or ""),
                space_separate_cjk(row[3] or ""),
                space_separate_cjk(row[4] or ""),
                space_separate_cjk(row[5] or ""),
                space_separate_cjk(row[6] or ""),
            ),
        )
    return len(rows)


def migrate_rows(conn: sqlite3.Connection) -> None:
    source_tables = table_names(conn, "old")
    target_tables = table_names(conn, "main")

    conn.execute("PRAGMA foreign_keys = OFF")
    for table in DELETE_ORDER:
        if table in target_tables:
            conn.execute(f"DELETE FROM main.{qident(table)}")

    for table in MIGRATED_TABLES:
        if table not in source_tables or table not in target_tables:
            print(f"skip: {table} missing in source or target")
            continue
        source_cols = table_columns(conn, table, "old")
        target_cols = table_columns(conn, table, "main")
        columns = [col for col in target_cols if col in source_cols]
        if not columns:
            print(f"skip: {table} has no common columns")
            continue
        col_sql = ", ".join(qident(col) for col in columns)
        conn.execute(
            f"""
            INSERT INTO main.{qident(table)} ({col_sql})
            SELECT {col_sql}
            FROM old.{qident(table)}
            """
        )
        print(f"migrated: {table} ({len(columns)} columns)")

    conn.execute("PRAGMA foreign_keys = ON")


def run(args: argparse.Namespace) -> None:
    source_root = Path(args.source_root).resolve()
    target_root = Path(args.target_root).resolve()
    source_db = Path(args.source_db).resolve()
    target_db = Path(args.target_db).resolve()

    if not source_db.exists():
        raise SystemExit(f"source database not found: {source_db}")

    if args.dry_run:
        source = sqlite3.connect(db_uri(source_db, readonly=True), uri=True)
        print(f"source: {source_db}")
        print_counts(source, MIGRATED_TABLES, "main")
        source.close()
        if target_db.exists():
            target = sqlite3.connect(target_db)
            print(f"target: {target_db}")
            print_counts(target, MIGRATED_TABLES, "main")
            target.close()
        else:
            print(f"target: {target_db} missing")
        return

    target_db.parent.mkdir(parents=True, exist_ok=True)
    backup_target(target_db)
    asyncio.run(ensure_target_schema(target_db))
    copy_skill_files(source_root, target_root)

    conn = sqlite3.connect(db_uri(target_db), uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute(f"ATTACH DATABASE ? AS old", (db_uri(source_db, readonly=True),))
    try:
        migrate_rows(conn)
        update_skill_paths(conn, source_root, target_root)
        fts_count = rebuild_memory_fts(conn)
        conn.commit()
        print(f"memory_fts: rebuilt {fts_count} rows")
    finally:
        conn.execute("DETACH DATABASE old")
        conn.close()

    print("migration complete")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate legacy Amadeus_web state into this project.")
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE_ROOT))
    parser.add_argument("--target-root", default=str(DEFAULT_TARGET_ROOT))
    parser.add_argument("--source-db", default=str(DEFAULT_SOURCE_DB))
    parser.add_argument("--target-db", default=str(DEFAULT_TARGET_DB))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
