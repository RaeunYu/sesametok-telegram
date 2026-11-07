from __future__ import annotations

import pytest

from sesametok_tg.cli import build_parser, main


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0


def test_version_exits_zero() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0


def test_subcommands_are_registered() -> None:
    parser = build_parser()
    for name in ("run", "setup", "status"):
        assert parser.parse_args([name]).command == name
