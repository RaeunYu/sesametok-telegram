"""로컬 상태 저장소(sqlite3).

백엔드와 별개로 이 클라이언트가 기억해야 하는 것은 넷뿐이다.

1. **연결(binding)** — 카카오톡 방 ↔ 텔레그램 그룹
2. **전달(delivery)** — 어떤 카카오톡 메시지를 어떤 텔레그램 메시지로 보냈는가(수정·중복 방지)
3. **커서** — 마지막으로 처리한 이벤트 `seq`, 방별 마지막 메시지 시각
4. **방 캐시** — 노출 여부·이름

**메시지 본문은 저장하지 않는다.** 유출면만 늘고, 이 프로젝트가 본문을 기억할 이유도 없다.
발신 텍스트는 메모리에만 둔다.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STATE_FILE = "state.sqlite3"
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rooms (
    chat_id         TEXT PRIMARY KEY,
    chat_name       TEXT NOT NULL,
    chat_type       TEXT NOT NULL,
    exposed         INTEGER NOT NULL DEFAULT 0,
    last_message_at TEXT,
    message_count   INTEGER
);

CREATE TABLE IF NOT EXISTS bindings (
    telegram_chat_id INTEGER PRIMARY KEY,
    kakao_chat_id    TEXT NOT NULL UNIQUE,
    linked_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deliveries (
    telegram_chat_id    INTEGER NOT NULL,
    telegram_message_id INTEGER NOT NULL,
    kakao_chat_id       TEXT NOT NULL,
    kakao_message_id    TEXT NOT NULL,
    seq                 INTEGER NOT NULL,
    created_at          TEXT NOT NULL,
    PRIMARY KEY (telegram_chat_id, telegram_message_id)
);

CREATE INDEX IF NOT EXISTS deliveries_kakao
    ON deliveries(kakao_chat_id, kakao_message_id);

CREATE TABLE IF NOT EXISTS room_cursors (
    chat_id TEXT PRIMARY KEY,
    cursor  TEXT NOT NULL
);
"""


def now_utc() -> str:
    """백엔드와 같은 형식(RFC3339, `+00:00`, 초 단위)의 현재 시각."""

    return datetime.now(UTC).replace(microsecond=0).isoformat()


class Store:
    """단일 프로세스에서 공유하는 sqlite3 저장소.

    이벤트 루프 스레드와 발신 워커가 함께 쓰므로 `check_same_thread=False`와 재진입 락을
    사용한다. 트랜잭션은 `with self._conn`이 커밋까지 책임진다.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- 스키마 -------------------------------------------------------------

    def _migrate(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # -- meta ---------------------------------------------------------------

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return default if row is None else str(row["value"])

    def set_meta(self, key: str, value: str | int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

    def get_int_meta(self, key: str, default: int | None = None) -> int | None:
        raw = self.get_meta(key)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    # -- 방 캐시 ------------------------------------------------------------

    def upsert_rooms(self, rooms: Iterable[dict[str, Any]]) -> int:
        rows = [
            (
                str(room["chat_id"]),
                str(room.get("chat_name") or ""),
                str(room.get("chat_type") or "direct"),
                1 if room.get("exposed") else 0,
                room.get("last_message_at"),
                room.get("message_count"),
            )
            for room in rooms
        ]
        if not rows:
            return 0
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO rooms (chat_id, chat_name, chat_type, exposed, last_message_at, message_count) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET "
                "  chat_name = excluded.chat_name,"
                "  chat_type = excluded.chat_type,"
                "  exposed = excluded.exposed,"
                "  last_message_at = excluded.last_message_at,"
                "  message_count = excluded.message_count",
                rows,
            )
        return len(rows)

    def set_exposed(self, chat_id: str, exposed: bool) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE rooms SET exposed = ? WHERE chat_id = ?",
                (1 if exposed else 0, str(chat_id)),
            )

    def room(self, chat_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM rooms WHERE chat_id = ?", (str(chat_id),)
            ).fetchone()
        return _room_dict(row)

    def rooms(self, *, exposed_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM rooms"
        if exposed_only:
            sql += " WHERE exposed = 1"
        sql += " ORDER BY chat_id"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [_room_dict(row) for row in rows if row is not None]

    # -- 연결 ---------------------------------------------------------------

    def bind(self, telegram_chat_id: int, kakao_chat_id: str) -> None:
        """그룹과 방을 연결한다. 같은 그룹/같은 방의 기존 연결은 대체한다."""

        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM bindings WHERE telegram_chat_id = ? OR kakao_chat_id = ?",
                (int(telegram_chat_id), str(kakao_chat_id)),
            )
            self._conn.execute(
                "INSERT INTO bindings (telegram_chat_id, kakao_chat_id, linked_at) VALUES (?, ?, ?)",
                (int(telegram_chat_id), str(kakao_chat_id), now_utc()),
            )

    def unbind(self, telegram_chat_id: int) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM bindings WHERE telegram_chat_id = ?", (int(telegram_chat_id),)
            )
        return bool(cursor.rowcount)

    def chat_of_room(self, kakao_chat_id: str) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT telegram_chat_id FROM bindings WHERE kakao_chat_id = ?",
                (str(kakao_chat_id),),
            ).fetchone()
        return None if row is None else int(row["telegram_chat_id"])

    def room_of_chat(self, telegram_chat_id: int) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT kakao_chat_id FROM bindings WHERE telegram_chat_id = ?",
                (int(telegram_chat_id),),
            ).fetchone()
        return None if row is None else str(row["kakao_chat_id"])

    def bindings(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM bindings ORDER BY linked_at"
            ).fetchall()
        return [dict(row) for row in rows]

    # -- 전달 기록 ----------------------------------------------------------

    def record_delivery(
        self,
        *,
        telegram_chat_id: int,
        telegram_message_id: int,
        kakao_chat_id: str,
        kakao_message_id: str,
        seq: int,
        created_at: str | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO deliveries (telegram_chat_id, telegram_message_id, kakao_chat_id, "
                "kakao_message_id, seq, created_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(telegram_chat_id, telegram_message_id) DO UPDATE SET "
                "  kakao_chat_id = excluded.kakao_chat_id,"
                "  kakao_message_id = excluded.kakao_message_id,"
                "  seq = excluded.seq,"
                "  created_at = excluded.created_at",
                (
                    int(telegram_chat_id),
                    int(telegram_message_id),
                    str(kakao_chat_id),
                    str(kakao_message_id),
                    int(seq),
                    created_at or now_utc(),
                ),
            )

    def delivery_for_kakao(
        self, kakao_chat_id: str, kakao_message_id: str
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM deliveries WHERE kakao_chat_id = ? AND kakao_message_id = ? "
                "ORDER BY seq LIMIT 1",
                (str(kakao_chat_id), str(kakao_message_id)),
            ).fetchone()
        return None if row is None else dict(row)

    def delivery_for_telegram(
        self, telegram_chat_id: int, telegram_message_id: int
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM deliveries WHERE telegram_chat_id = ? AND telegram_message_id = ?",
                (int(telegram_chat_id), int(telegram_message_id)),
            ).fetchone()
        return None if row is None else dict(row)

    # -- 방별 커서 ----------------------------------------------------------

    def set_room_cursor(self, chat_id: str, cursor: str) -> None:
        """`since`로 다시 조회할 때 쓸 마지막 메시지 시각(RFC3339, `+00:00`)."""

        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO room_cursors (chat_id, cursor) VALUES (?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET cursor = excluded.cursor",
                (str(chat_id), str(cursor)),
            )

    def room_cursor(self, chat_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT cursor FROM room_cursors WHERE chat_id = ?", (str(chat_id),)
            ).fetchone()
        return None if row is None else str(row["cursor"])


def _room_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "chat_id": str(row["chat_id"]),
        "chat_name": str(row["chat_name"]),
        "chat_type": str(row["chat_type"]),
        "exposed": bool(row["exposed"]),
        "last_message_at": row["last_message_at"],
        "message_count": row["message_count"],
    }
