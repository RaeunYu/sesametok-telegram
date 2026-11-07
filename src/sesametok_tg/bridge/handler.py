"""텔레그램 업데이트 처리: 권한 → 명령/콜백 → 응답.

권한은 **소유자 user_id 하나**로 판단한다. 그 밖의 업데이트는 응답도 저장도 하지 않는다.
그룹 안의 명령은 `/link@MyBot`처럼 봇 이름이 붙어 오므로 `commands.parse_command`가 떼어낸다.
"""

from __future__ import annotations

import logging
from typing import Any

from ..backend import BackendClient, BackendError
from ..store import Store
from ..telegram.api import TelegramBot, TelegramError
from ..telegram.commands import Command, parse_command
from .binding import BindingService
from .render import render, split_text

logger = logging.getLogger(__name__)

HELP_TEXT = """명령
/link — 이 그룹을 카카오톡 방에 연결
/unlink — 이 그룹의 연결 해제
/history [n] — 이 방의 최근 메시지 n건(기본 20)
/status — 연결·백엔드 상태
/help — 이 목록

그룹은 비공개로 유지하세요. 공개 사용자명을 만들면 그룹이 검색에 노출됩니다."""

DEFAULT_HISTORY = 20
MAX_HISTORY = 50


class UpdateHandler:
    def __init__(
        self,
        *,
        owner_user_id: int,
        store: Store,
        client: BackendClient,
        bot: TelegramBot,
        bindings: BindingService,
    ) -> None:
        self.owner_user_id = owner_user_id
        self._store = store
        self._client = client
        self._bot = bot
        self._bindings = bindings

    # -- 진입점 -------------------------------------------------------------

    def handle_update(self, update: dict[str, Any]) -> None:
        if isinstance(update.get("callback_query"), dict):
            self._handle_callback(update["callback_query"])
        elif isinstance(update.get("message"), dict):
            self._handle_message(update["message"])

    # -- 내부 ---------------------------------------------------------------

    def _is_owner(self, user: Any) -> bool:
        return isinstance(user, dict) and user.get("id") == self.owner_user_id

    def _handle_message(self, message: dict[str, Any]) -> None:
        if not self._is_owner(message.get("from")):
            logger.info("소유자가 아닌 사용자의 업데이트를 무시했습니다")
            return

        command = parse_command(message.get("text"))
        if command is None:
            return  # 평문 발신은 M4에서 연결한다

        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not isinstance(chat_id, int):
            return

        handler = {
            "start": self._cmd_help,
            "help": self._cmd_help,
            "link": self._cmd_link,
            "unlink": self._cmd_unlink,
            "history": self._cmd_history,
            "status": self._cmd_status,
        }.get(command.name)
        if handler is None:
            self._send(chat_id, "모르는 명령입니다. /help를 보세요.")
            return
        handler(chat, command)

    def _handle_callback(self, callback: dict[str, Any]) -> None:
        if not self._is_owner(callback.get("from")):
            return
        data = str(callback.get("data") or "")
        if not data.startswith("link:"):
            return

        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        message_id = message.get("message_id")
        callback_id = str(callback.get("id") or "")

        if not isinstance(chat_id, int) or chat.get("type") == "private":
            self._answer(callback_id, "그룹에서만 연결할 수 있습니다")
            return

        kakao_chat_id = data[len("link:") :]
        room_name = self._bindings.bind(chat_id, kakao_chat_id)
        if room_name is None:
            self._answer(callback_id, "연결할 수 없는 방입니다")
            return

        self._answer(callback_id, "연결했습니다")
        if isinstance(message_id, int):
            self._edit(
                chat_id,
                message_id,
                f"«{room_name}» 연결됨\n이 그룹에 이 방의 메시지가 도착합니다.",
            )
        else:
            self._send(chat_id, f"«{room_name}» 연결됨")

    # -- 명령 ---------------------------------------------------------------

    def _cmd_help(self, chat: dict[str, Any], command: Command) -> None:
        self._send(chat["id"], HELP_TEXT)

    def _cmd_link(self, chat: dict[str, Any], command: Command) -> None:
        if chat.get("type") == "private":
            self._send(
                chat["id"],
                "그룹에서 실행해 주세요. 카카오톡 방마다 그룹을 만들고 봇을 초대한 뒤 /link를 쓰세요.",
            )
            return
        try:
            self._bindings.refresh()
        except BackendError as exc:
            self._send(chat["id"], f"백엔드에서 방 목록을 받지 못했습니다({exc.code}).")
            return

        options = self._bindings.options()
        if not options:
            self._send(
                chat["id"],
                "연결할 수 있는 방이 없습니다. 백엔드 메뉴바의 '노출 방 관리…'에서 방을 먼저 지정하세요.",
            )
            return

        keyboard = {
            "inline_keyboard": [
                [{"text": option.chat_name, "callback_data": f"link:{option.chat_id}"}]
                for option in options
            ]
        }
        self._send(chat["id"], "어느 카카오톡 방에 연결할까요?", reply_markup=keyboard)

    def _cmd_unlink(self, chat: dict[str, Any], command: Command) -> None:
        room_name = self._bindings.unbind(chat["id"])
        if room_name is None:
            self._send(chat["id"], "이 그룹은 연결되어 있지 않습니다.")
            return
        self._send(chat["id"], f"«{room_name}» 연결을 해제했습니다.")

    def _cmd_history(self, chat: dict[str, Any], command: Command) -> None:
        kakao_chat_id = self._store.room_of_chat(chat["id"])
        if kakao_chat_id is None:
            self._send(chat["id"], "먼저 /link로 이 그룹을 방에 연결하세요.")
            return

        limit = DEFAULT_HISTORY
        if command.argument.isdigit():
            limit = max(1, min(MAX_HISTORY, int(command.argument)))

        try:
            page = self._client.messages(kakao_chat_id, limit=limit)
        except BackendError as exc:
            self._send(chat["id"], f"히스토리를 받지 못했습니다({exc.code}).")
            return

        messages = page.get("messages") or []
        if not messages:
            self._send(chat["id"], "메시지가 없습니다.")
            return

        block = "\n".join(render(message) for message in messages)
        for part in split_text(block):
            self._send(chat["id"], part)

    def _cmd_status(self, chat: dict[str, Any], command: Command) -> None:
        lines = [f"백엔드: {self._client.base_url}"]
        try:
            health = self._client.health()
            lines.append("상태: " + ("정상" if health.get("ok") else "폴링 실패"))
            status = self._client.status()
            event_seq = (status.get("archive") or {}).get("event_seq")
            if event_seq is not None:
                lines.append(f"이벤트 seq: {event_seq}")
        except BackendError as exc:
            lines.append(f"상태: 연결 실패({exc.code})")

        exposed = len(self._store.rooms(exposed_only=True))
        bound = len(self._store.bindings())
        lines.append(f"노출 방 {exposed}개 · 연결 {bound}개")
        room_name = self._bindings.room_name(chat["id"])
        lines.append(f"이 그룹: «{room_name}»" if room_name else "이 그룹: 연결 안 됨")
        self._send(chat["id"], "\n".join(lines))

    # -- 전송 헬퍼 ----------------------------------------------------------

    def _send(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        try:
            self._bot.send_message(chat_id, text, reply_markup=reply_markup)
        except TelegramError as exc:
            logger.warning("텔레그램 전송 실패(chat_id=%s, code=%s)", chat_id, exc.code)

    def _edit(self, chat_id: int, message_id: int, text: str) -> None:
        try:
            self._bot.edit_message_text(chat_id, message_id, text)
        except TelegramError as exc:
            logger.warning("텔레그램 수정 실패(chat_id=%s, code=%s)", chat_id, exc.code)

    def _answer(self, callback_id: str, text: str) -> None:
        try:
            self._bot.answer_callback_query(callback_id, text)
        except TelegramError as exc:
            logger.warning("콜백 응답 실패(code=%s)", exc.code)
