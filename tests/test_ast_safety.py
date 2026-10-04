"""Structural proofs that the concurrency model is sound.

The bot shares ONE SQLite connection between concurrent updates. That is safe only
because no coroutine ever suspends while a transaction is open, and because the
pre-checkout handler runs check_payable and extend_reservation without a suspension
point between them. Both are enforced here by reading the source."""

from __future__ import annotations

import ast
import shutil
from collections.abc import Iterator
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent / "bot"
TRANSACTION_HELPERS = {"transaction", "immediate_transaction"}
SUSPENSIONS = (ast.Await, ast.AsyncWith, ast.AsyncFor)


def _helper_name(call: ast.expr) -> str | None:
    if not isinstance(call, ast.Call):
        return None
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_transaction_block(node: ast.AST) -> bool:
    return isinstance(node, ast.With) and any(
        _helper_name(item.context_expr) in TRANSACTION_HELPERS for item in node.items
    )


def _walk_same_scope(node: ast.AST) -> Iterator[ast.AST]:
    """Every node inside `node`, without entering nested function or lambda bodies
    (code defined there does not run while the block does)."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(current))


def scan_transactions(root: Path) -> tuple[int, list[str]]:
    """(number of transaction blocks scanned, "file:line" of each suspension point
    found inside one) for every .py file under `root`."""
    blocks = 0
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not _is_transaction_block(node):
                continue
            blocks += 1
            for inner in _walk_same_scope(node):
                is_async_comprehension = isinstance(inner, ast.comprehension) and inner.is_async
                if isinstance(inner, SUSPENSIONS) or is_async_comprehension:
                    violations.append(f"{path.name}:{getattr(inner, 'lineno', 0)}")
    return blocks, violations


# --- the transaction rule ----------------------------------------------------------------------


def test_no_await_async_with_or_async_for_inside_any_transaction_block():
    blocks, violations = scan_transactions(BOT_DIR)
    assert violations == [], f"suspension point inside an open transaction: {violations}"
    assert blocks >= 30  # the scan really looked at the repo layer (40 blocks at the time)


def test_the_scan_is_not_vacuous_it_fails_on_a_copy_with_an_await_in_a_transaction(tmp_path):
    """Copy bot/ to a temp dir, inject an await into a real `with immediate_transaction()`
    block of the COPY, and show the scanner now reports it. The real source is never
    touched."""
    copy = tmp_path / "bot"
    shutil.copytree(BOT_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
    target = copy / "repo" / "orders.py"
    lines = target.read_text(encoding="utf-8").split("\n")
    index = next(
        i for i, line in enumerate(lines) if "with immediate_transaction() as conn:" in line
    )
    indent = " " * (len(lines[index]) - len(lines[index].lstrip()) + 4)
    lines.insert(index + 1, f"{indent}await asyncio.sleep(0)  # injected by the test")
    target.write_text("\n".join(lines), encoding="utf-8")

    _, violations = scan_transactions(copy)
    assert violations and violations[0].startswith("orders.py:")

    _, clean = scan_transactions(BOT_DIR)  # while the real tree is still clean
    assert clean == []


def test_the_scanner_catches_each_kind_of_suspension(tmp_path):
    def case(*body_lines: str) -> str:
        return "\n".join(["async def f():", *body_lines, ""])

    cases = {
        "await_": case("    with transaction() as c:", "        await g()"),
        "async_with": case(
            "    with transaction() as c:", "        async with x:", "            pass"
        ),
        "async_for": case(
            "    with immediate_transaction() as c:",
            "        async for _ in x:",
            "            pass",
        ),
        "attr_call": case("    with db.transaction() as c:", "        await g()"),
        "comprehension": case("    with transaction() as c:", "        y = [i async for i in x]"),
    }
    for name, source in cases.items():
        folder = tmp_path / name
        folder.mkdir()
        (folder / "m.py").write_text(source, encoding="utf-8")
        blocks, violations = scan_transactions(folder)
        assert blocks == 1 and len(violations) == 1, name


def test_the_scanner_ignores_awaits_outside_a_transaction_and_in_nested_functions(tmp_path):
    source = (
        "async def f():\n"
        "    await before()\n"
        "    with transaction() as c:\n"
        "        c.execute('x')\n"
        "        async def later():\n"
        "            await not_run_inside_the_block()\n"
        "    await after()\n"
    )
    (tmp_path / "m.py").write_text(source, encoding="utf-8")
    assert scan_transactions(tmp_path) == (1, [])


# --- the pre-checkout rule -----------------------------------------------------------------------


def _calls_named(tree: ast.AST, name: str) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _helper_name(n) == name]


def awaits_between(source: str, function: str, first: str, second: str) -> list[int]:
    """Line numbers of Await nodes positioned after the end of the `first` call and
    before the start of the `second` call, inside `function`."""
    tree = ast.parse(source)
    func = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function
    )
    (start_call,) = _calls_named(func, first)
    (end_call,) = _calls_named(func, second)
    start = (start_call.end_lineno, start_call.end_col_offset)
    end = (end_call.lineno, end_call.col_offset)
    assert start < end, f"{first} must come before {second}"
    return [
        n.lineno
        for n in ast.walk(func)
        if isinstance(n, ast.Await) and start <= (n.lineno, n.col_offset) < end
    ]


def test_pre_checkout_has_no_await_between_check_payable_and_extend_reservation():
    source = (BOT_DIR / "handlers" / "payments.py").read_text(encoding="utf-8")
    assert awaits_between(source, "pre_checkout", "check_payable", "extend_reservation") == []


def test_the_pre_checkout_check_can_detect_an_await_in_between():
    broken = (
        "async def pre_checkout(update, context):\n"
        "    rejection = check_payable(order)\n"
        "    await something()\n"
        "    extend_reservation(order_id, 2)\n"
    )
    assert awaits_between(broken, "pre_checkout", "check_payable", "extend_reservation") == [3]
    fine = (
        "async def pre_checkout(update, context):\n"
        "    rejection = check_payable(order)\n"
        "    extend_reservation(order_id, 2)\n"
        "    await query.answer(ok=True)\n"
    )
    assert awaits_between(fine, "pre_checkout", "check_payable", "extend_reservation") == []
