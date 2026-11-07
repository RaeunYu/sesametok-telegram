"""CLI 진입점.

M0 단계에서는 `setup`/`run`이 아직 구현 전임을 명확히 알리고, `status`는 설정만 요약한다.
브리지 루프는 M2(SSE·저장소)부터 채운다.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import ConfigError, Settings, load_settings, read_backend_token
from .logs import configure_logging

_NOT_YET = "아직 구현되지 않았습니다(구현 계획 M{stage})."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sesametok-telegram",
        description="카카오톡 아카이브를 텔레그램으로 옮기는 클라이언트",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="브리지를 포그라운드에서 실행한다")
    sub.add_parser("setup", help="봇 토큰·소유자 id를 받아 설정 파일을 만든다")
    sub.add_parser("status", help="설정과 백엔드 연결 상태를 요약한다")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run":
            return _cmd_run()
        if args.command == "setup":
            print(_NOT_YET.format(stage=5), file=sys.stderr)
            return 3
        if args.command == "status":
            return _cmd_status()
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2
    return 0


def _cmd_run() -> int:
    settings = load_settings()
    configure_logging(settings.log_dir, secrets=(settings.bot_token,))
    print(_NOT_YET.format(stage=2), file=sys.stderr)
    return 3


def _cmd_status() -> int:
    settings = load_settings()
    for line in _summary(settings):
        print(line)
    return 0


def _summary(settings: Settings) -> list[str]:
    """비밀값 없이 보여줄 수 있는 것만 요약한다."""

    token_state = "설정됨" if _token_exists(settings) else "없음"
    return [
        f"백엔드      : {settings.backend_base_url}",
        f"소유자 id   : {settings.owner_user_id}",
        f"설정 파일   : {settings.config_path}",
        f"데이터 디렉터리: {settings.data_dir}",
        f"로그 디렉터리  : {settings.log_dir}",
        f"백엔드 토큰 : {token_state} ({settings.backend_token_path})",
    ]


def _token_exists(settings: Settings) -> bool:
    try:
        read_backend_token(settings.backend_token_path)
    except ConfigError:
        return False
    return True


if __name__ == "__main__":
    sys.exit(main())
