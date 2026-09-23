"""对话历史持久化: SQLite 存储层。

每个 session 一份完整 history (含 tool_calls/tool 结果), 跨重启保留。
默认 db 路径: ~/.aiagent/memory.db
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .memory import Memory, Message

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    title      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    msg_count  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    session_id    TEXT NOT NULL,
    seq           INTEGER NOT NULL,
    role          TEXT NOT NULL,
    content       TEXT,
    name          TEXT,
    tool_calls    TEXT,           -- JSON 数组
    tool_call_id  TEXT,
    timestamp     TEXT,
    PRIMARY KEY (session_id, seq),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
"""


class MemoryStore:
    """SQLite 存储层。一个 db 文件里可以有多个 session_id。"""

    def __init__(self, db_path: str | Path = "~/.aiagent/memory.db"):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        # detect_types 让 json 字段自动转 (虽然这里用手动 json.loads)
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    # ---- sessions ----
    def list_sessions(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT session_id, title, created_at, updated_at, msg_count "
                "FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [
            {
                "session_id": r[0],
                "title": r[1] or r[0],
                "created_at": r[2],
                "updated_at": r[3],
                "msg_count": r[4],
            }
            for r in rows
        ]

    def get_session(self, session_id: str) -> dict | None:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT session_id, title, created_at, updated_at, msg_count "
                "FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if not r:
            return None
        return {
            "session_id": r[0],
            "title": r[1] or r[0],
            "created_at": r[2],
            "updated_at": r[3],
            "msg_count": r[4],
        }

    def delete_session(self, session_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
            conn.commit()
            return cur.rowcount > 0

    # ---- messages ----
    def has_messages(self, session_id: str) -> bool:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT 1 FROM messages WHERE session_id = ? LIMIT 1",
                (session_id,),
            ).fetchone()
        return r is not None

    def load(self, session_id: str, max_messages: int = 30, max_tokens: int = 6000) -> Memory | None:
        """载入指定 session 的完整消息列表, 包成 Memory 返回。

        返回 None 表示 session 不存在或没消息。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, name, tool_calls, tool_call_id, timestamp "
                "FROM messages WHERE session_id = ? ORDER BY seq ASC",
                (session_id,),
            ).fetchall()
        if not rows:
            return None

        mem = Memory(max_messages=max_messages, max_tokens=max_tokens)
        for r in rows:
            tc = json.loads(r[3]) if r[3] else None
            mem._messages.append(Message(
                role=r[0],
                content=r[1],
                name=r[2],
                tool_calls=tc,
                tool_call_id=r[4],
                timestamp=r[5] or "",
            ))
        return mem

    def save(
        self,
        session_id: str,
        memory: Memory,
        title: str | None = None,
    ) -> None:
        """保存一个 session 的完整消息列表 (覆盖式: 先删后写)。

        session 记录 upsert; updated_at 用 now; title 首次保存时用传入值, 之后保留。
        """
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        msgs = memory.messages()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT title FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO sessions(session_id, title, created_at, updated_at, msg_count) "
                    "VALUES(?, ?, ?, ?, ?)",
                    (session_id, title or session_id, now, now, len(msgs)),
                )
            else:
                kept_title = existing[0]
                if title and not kept_title.startswith("auto-"):
                    kept_title = title
                conn.execute(
                    "UPDATE sessions SET updated_at = ?, msg_count = ?, title = ? "
                    "WHERE session_id = ?",
                    (now, len(msgs), kept_title, session_id),
                )
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            for i, m in enumerate(msgs):
                tc_json = json.dumps(m.tool_calls, ensure_ascii=False) if m.tool_calls else None
                conn.execute(
                    "INSERT INTO messages(session_id, seq, role, content, name, "
                    "tool_calls, tool_call_id, timestamp) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (session_id, i, m.role, m.content, m.name, tc_json,
                     m.tool_call_id, m.timestamp),
                )
            conn.commit()