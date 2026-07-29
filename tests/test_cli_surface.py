"""
Characterization tests for the `cli.py` command surface.

Why this exists: `cli.py` is ~3,450 lines carrying 28 subcommands, and **not
one of its command entry points had any test coverage**. That is the file
where merge conflicts land, and it is the file most in need of being split
into modules — but splitting it safely requires a net that fails loudly if a
command silently disappears, loses its handler, or stops parsing.

These tests deliberately assert *structure*, not behaviour. They never invoke
a handler, so nothing here touches a database, a drive, or an audio file. They
pin exactly the properties a refactor is most likely to break:

  * every advertised subcommand still exists,
  * each one is still wired to a callable handler via ``set_defaults(func=...)``,
  * each one's parser still builds and still accepts ``--help``,
  * the handlers are real module-level functions, not stale references.

If the split later moves handlers into modules, these should keep passing
untouched — that is the point.
"""
import argparse
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cli


# The commands FableGear ships. Pinned as a literal so that *removing* one is a
# test failure rather than a silently smaller set — a set derived from the
# parser itself could never catch a deletion.
EXPECTED_COMMANDS = {
    "anlz-read", "audit", "convert", "dead-files", "duplicates", "export-audit",
    "export-onelibrary", "import", "link", "novelty", "organize", "pdb-read",
    "pioneer-settings", "process", "prune", "rekordbox-dedupe", "rekordbox-sync",
    "relocate", "rename", "setup", "usb-inspect",
}


def _subparsers(parser: argparse.ArgumentParser):
    """The single subparsers action registered on the root parser."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    pytest.fail("cli._build_parser() registered no subparsers")


@pytest.fixture(scope="module")
def parser():
    return cli._build_parser()


@pytest.fixture(scope="module")
def choices(parser):
    return _subparsers(parser).choices


def test_parser_builds(parser):
    assert isinstance(parser, argparse.ArgumentParser)


def test_every_expected_command_is_registered(choices):
    missing = EXPECTED_COMMANDS - set(choices)
    assert not missing, f"subcommands disappeared from the CLI: {sorted(missing)}"


def test_no_command_appeared_without_being_declared_here(choices):
    """Keeps EXPECTED_COMMANDS honest: a new subcommand must be added above,
    which forces a moment's thought about whether it needs its own coverage."""
    extra = set(choices) - EXPECTED_COMMANDS
    assert not extra, (
        f"new subcommand(s) {sorted(extra)} — add them to EXPECTED_COMMANDS "
        "and consider whether they need real behavioural tests"
    )


@pytest.mark.parametrize("name", sorted(EXPECTED_COMMANDS))
def test_command_is_wired_to_a_callable_handler(choices, name):
    """The exact breakage a refactor causes: the parser still knows the command,
    but `set_defaults(func=...)` points at nothing, so `main()` raises
    AttributeError at dispatch instead of running."""
    sub = choices.get(name)
    assert sub is not None, f"{name} is not registered"

    # Command *groups* carry their handlers on their children, not themselves.
    # `playlist` is the known one; it lands with the export-parity work, at
    # which point test_no_command_appeared_without_being_declared_here fails
    # and forces it into EXPECTED_COMMANDS — and this branch starts running.
    if name == "playlist":
        group = _subparsers(sub).choices
        assert group, "playlist should expose subcommands"
        for child_name, child in group.items():
            fn = child.get_default("func")
            assert callable(fn), f"playlist {child_name} has no callable handler"
        return

    fn = sub.get_default("func")
    assert fn is not None, f"{name} has no `func` default — main() would fail at dispatch"
    assert callable(fn), f"{name}'s handler is not callable: {fn!r}"


@pytest.mark.parametrize("name", sorted(EXPECTED_COMMANDS))
def test_command_help_renders(choices, name):
    """Catches a parser that builds at import time but blows up when actually
    formatting — e.g. a bad `%` in a help string, or a broken default."""
    assert choices[name].format_help()


def test_handlers_are_module_level_functions(choices):
    """Every handler should resolve back to a real `cli` attribute. A handler
    that is a closure or a stale import is a sign the split went wrong."""
    for name, sub in choices.items():
        fn = sub.get_default("func")
        if fn is None:      # command groups (e.g. playlist) handled above
            continue
        assert getattr(cli, fn.__name__, None) is not None, (
            f"{name}'s handler {fn.__name__} is not reachable as cli.{fn.__name__}"
        )


def test_root_parser_rejects_an_unknown_command(parser):
    with pytest.raises(SystemExit):
        parser.parse_args(["definitely-not-a-command"])


def test_dispatch_contract_holds(parser):
    """`main()` does `args.func(args)`, so a parsed command must always carry a
    callable `func`. Verified without invoking the handler."""
    args = parser.parse_args(["audit"])
    assert callable(getattr(args, "func", None))
