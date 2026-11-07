"""그룹 ↔ 방 연결.

노출(exposed)은 **백엔드**가 소유하고, 연결(binding)은 **클라이언트**가 소유한다. 연결은
노출된 방에만 성립한다 — "보여주기로 한 방"에만 텔레그램 그룹을 붙이는 것이 이 프로젝트의 계약이다.
백엔드는 텔레그램 `chat_id`를 알지 못한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..backend import BackendClient
from ..store import Store

# 텔레그램 callback_data 상한은 64바이트다. `link:` 접두를 빼고 채팅 id를 넣을 여유를 둔다.
MAX_CALLBACK_PAYLOAD = 60
MAX_OPTIONS = 25
UNKNOWN_ROOM = "알 수 없는 방"


@dataclass(frozen=True)
class RoomOption:
    chat_id: str
    chat_name: str


class BindingService:
    def __init__(self, store: Store, client: BackendClient) -> None:
        self._store = store
        self._client = client

    def refresh(self) -> int:
        """백엔드에서 노출된 방 목록을 받아 캐시를 갱신한다."""

        return self._store.upsert_rooms(self._client.rooms(exposed_only=True))

    def options(self) -> list[RoomOption]:
        """아직 연결되지 않은 노출 방(콜백 데이터가 들어가는 것만)."""

        bound = {binding["kakao_chat_id"] for binding in self._store.bindings()}
        options = [
            RoomOption(room["chat_id"], room["chat_name"])
            for room in self._store.rooms(exposed_only=True)
            if room["chat_id"] not in bound and len(room["chat_id"]) <= MAX_CALLBACK_PAYLOAD
        ]
        return options[:MAX_OPTIONS]

    def bind(self, telegram_chat_id: int, kakao_chat_id: str) -> str | None:
        room = self._store.room(kakao_chat_id)
        if room is None or not room["exposed"]:
            # 클라이언트가 재시작해 캐시가 비었을 수 있다(버튼은 텔레그램에 남아 있다).
            self.refresh()
            room = self._store.room(kakao_chat_id)
        if room is None or not room["exposed"]:
            return None
        self._store.bind(telegram_chat_id, kakao_chat_id)
        return str(room["chat_name"])

    def unbind(self, telegram_chat_id: int) -> str | None:
        kakao_chat_id = self._store.room_of_chat(telegram_chat_id)
        if kakao_chat_id is None:
            return None
        self._store.unbind(telegram_chat_id)
        return self._display_name(kakao_chat_id)

    def room_name(self, telegram_chat_id: int) -> str | None:
        kakao_chat_id = self._store.room_of_chat(telegram_chat_id)
        if kakao_chat_id is None:
            return None
        return self._display_name(kakao_chat_id)

    def _display_name(self, kakao_chat_id: str) -> str:
        room = self._store.room(kakao_chat_id)
        return str(room["chat_name"]) if room else UNKNOWN_ROOM
