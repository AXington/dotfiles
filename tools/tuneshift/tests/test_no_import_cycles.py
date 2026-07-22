"""Import-cycle regression guards (ARCH-M2).

``tuneshift.types`` exists to break the ``db`` <-> ``planapply`` and
``db`` <-> ``matching`` cycles: ``db.py`` returns ``ReviewItem`` and
``JournalEntry`` while both of those packages depend on ``db.Database``. The
shared records were moved to ``tuneshift.types`` so every side depends on it
instead of on each other, letting ``db.py`` import them at module scope rather
than lazily inside methods.

These tests fail if:
- ``tuneshift.types`` grows a dependency on any other ``tuneshift`` module
  (which would reintroduce a cycle), or
- ``db.py`` reverts to a lazy in-method import of either record, or
- the re-export identities drift so the same class is defined twice.
"""

import ast
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent
_TUNESHIFT = _PKG_ROOT / "tuneshift"


def _module_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), str(path))
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            targets.append(node.module)
        elif isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
    return targets


def test_types_module_has_no_tuneshift_imports() -> None:
    """The shared-types module must stay dependency-free within the package.

    A single ``tuneshift.*`` import here would recreate the very cycle the
    module exists to prevent.
    """
    imports = _module_imports(_TUNESHIFT / "types.py")
    offenders = [name for name in imports if name.startswith("tuneshift")]
    assert not offenders, f"tuneshift/types.py must not import tuneshift.*: {offenders}"


def test_db_imports_shared_records_at_module_scope() -> None:
    """db.py imports ReviewItem/JournalEntry from tuneshift.types at top level,
    not lazily inside a method (the ARCH-M2 smell we removed)."""
    tree = ast.parse((_TUNESHIFT / "db.py").read_text(), "db.py")
    top_level_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "tuneshift.types":
            top_level_names.update(alias.name for alias in node.names)
    assert {"ReviewItem", "JournalEntry"} <= top_level_names

    # No function/method may re-import them lazily.
    lazy: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {
            "tuneshift.matching",
            "tuneshift.planapply.models",
        }:
            names = {alias.name for alias in node.names}
            if names & {"ReviewItem", "JournalEntry"}:
                lazy.append(f"{node.module}:{node.lineno}")
    assert not lazy, f"db.py still lazily imports shared records: {lazy}"


def test_shared_records_have_single_definition() -> None:
    """The re-export paths resolve to the one canonical class object."""
    from tuneshift.db import JournalEntry as db_journal
    from tuneshift.db import ReviewItem as db_review
    from tuneshift.matching import ReviewItem as matching_review
    from tuneshift.planapply.models import JournalEntry as planapply_journal
    from tuneshift.types import JournalEntry as types_journal
    from tuneshift.types import ReviewItem as types_review

    assert db_review is types_review is matching_review
    assert db_journal is types_journal is planapply_journal
