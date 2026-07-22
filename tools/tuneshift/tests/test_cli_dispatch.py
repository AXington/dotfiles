"""Regression net for the CLI parser and command dispatch.

These tests pin the public CLI surface before and after the ``main`` dispatch
refactor (Task 4.1): every subcommand must expose ``--help`` and every
subcommand must be reachable through the dispatch table.
"""

from __future__ import annotations

import argparse
import importlib

import pytest

from tuneshift.cli import build_parser, main


def _subcommand_names() -> list[str]:
    parser = build_parser()
    subparsers = [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    assert subparsers, "expected a subparsers action on the root parser"
    return sorted(subparsers[0].choices.keys())


SUBCOMMANDS = _subcommand_names()


def test_subcommands_are_present() -> None:
    # Guards against an accidental drop of a whole command group.
    assert len(SUBCOMMANDS) >= 46


@pytest.mark.parametrize("name", SUBCOMMANDS)
def test_every_subcommand_exposes_help(name: str) -> None:
    """``<cmd> --help`` must parse and exit cleanly for every subcommand."""
    with pytest.raises(SystemExit) as excinfo:
        main([name, "--help"])
    assert excinfo.value.code == 0


def test_no_command_prints_help_and_returns_zero() -> None:
    assert main([]) == 0


def test_dispatch_table_covers_every_subcommand() -> None:
    """Every parser subcommand must be reachable via the dispatch tables."""
    cli = importlib.import_module("tuneshift.cli")
    simple = getattr(cli, "_SIMPLE_COMMANDS", {})
    special = getattr(cli, "_SPECIAL_COMMANDS", {})
    covered = set(simple) | set(special)
    missing = set(SUBCOMMANDS) - covered
    assert not missing, f"subcommands with no dispatch entry: {sorted(missing)}"


def test_simple_dispatch_targets_are_importable() -> None:
    """Each ``(module, attr)`` simple-dispatch entry must resolve."""
    cli = importlib.import_module("tuneshift.cli")
    simple = getattr(cli, "_SIMPLE_COMMANDS", {})
    for command, (module_name, attr) in simple.items():
        module = importlib.import_module(module_name)
        assert hasattr(module, attr), f"{command}: {module_name}.{attr} missing"
