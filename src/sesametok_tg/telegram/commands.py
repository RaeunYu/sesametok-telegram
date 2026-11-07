"""명령 파싱(순수 함수).

그룹에서 오는 명령은 `/link@MyBot`처럼 봇 이름이 붙는다. 텔레그램은 그것을 봇별로 걸러주지
않으므로 여기서 떼어낸다.
"""

from __future__ import annotations

from dataclasses import dataclass

KNOWN_COMMANDS = ("start", "help", "link", "unlink", "history", "status")


@dataclass(frozen=True)
class Command:
    name: str
    argument: str = ""


def parse_command(text: str | None) -> Command | None:
    if not text:
        return None
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    head, _, rest = stripped.partition(" ")
    name = head[1:].split("@", 1)[0].strip().lower()
    if not name:
        return None
    return Command(name=name, argument=rest.strip())
