"""Small executable guardrails for VtNote's incremental module boundaries."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "vtnote"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_application_contracts_do_not_depend_on_persistence() -> None:
    forbidden = {"sqlalchemy", "vtnote.database", "vtnote.models"}
    for path in (PACKAGE / "application").glob("*.py"):
        imported = _imports(path)
        assert not any(
            module == blocked or module.startswith(f"{blocked}.")
            for module in imported
            for blocked in forbidden
        ), f"{path.name} crossed the application/persistence boundary"


def test_http_transport_does_not_leak_into_core_modules() -> None:
    allowed = {PACKAGE / "api.py"}
    offenders: list[str] = []
    for path in PACKAGE.glob("*.py"):
        if path in allowed:
            continue
        if any(module.startswith("vtnote.http") for module in _imports(path)):
            offenders.append(path.name)
    assert offenders == []


def test_composition_hotspots_cannot_silently_grow_again() -> None:
    budgets = {
        PACKAGE / "api.py": 1_400,
        PACKAGE / "configuration.py": 1_325,
        PACKAGE / "tasks.py": 1_500,
        ROOT / "frontend" / "src" / "pages" / "TaskHistoryPage.tsx": 400,
        ROOT / "frontend" / "src" / "styles" / "components.css": 10,
        ROOT / "frontend" / "src" / "styles" / "features" / "create-task.css": 10,
        ROOT / "frontend" / "src" / "styles" / "features" / "task-library.css": 10,
        ROOT / "frontend" / "src" / "styles" / "features" / "task-detail.css": 10,
        ROOT / "frontend" / "src" / "styles" / "features" / "settings.css": 10,
    }
    for path, maximum in budgets.items():
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        assert line_count <= maximum, (
            f"{path.relative_to(ROOT)} has {line_count} lines; "
            f"split the new responsibility before exceeding {maximum}"
        )


def test_feature_style_entries_only_compose_bounded_parts() -> None:
    styles = ROOT / "frontend" / "src" / "styles" / "features"
    for name in ("create-task", "task-library", "task-detail", "settings"):
        entry = styles / f"{name}.css"
        lines = entry.read_text(encoding="utf-8").splitlines()
        assert lines and all(line.startswith('@import "./') for line in lines)
        for path in (styles / name).glob("*.css"):
            line_count = len(path.read_text(encoding="utf-8").splitlines())
            assert line_count <= 500, (
                f"{path.relative_to(ROOT)} has {line_count} lines; "
                "split the style responsibility before exceeding 500"
            )


def test_native_dialog_markup_is_owned_by_modal_dialog() -> None:
    source = ROOT / "frontend" / "src"
    owners = [
        path.relative_to(source).as_posix()
        for path in source.rglob("*.tsx")
        if "<dialog" in path.read_text(encoding="utf-8")
    ]
    assert owners == ["components/ModalDialog.tsx"]
