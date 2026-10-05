"""The README must not drift from the code it describes: the order lifecycle diagram,
the configuration table, the files it names, the tests it cites and its stated
verification status. The checkers are plain functions so each can be shown to FAIL on an
edited temporary copy of the README (the real file is never edited)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bot.config import Settings
from bot.repo.orders import ALLOWED_TRANSITIONS

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
CHANGELOG = ROOT / "CHANGELOG.md"

# The owner's setting for the Verification status wording (INVOICE_CHECKED_LIVE).
INVOICE_CHECKED_LIVE = True
VERIFICATION_LIVE = (
    "Stars mode: invoice creation has been checked against live Telegram. Pre-checkout, "
    "payment confirmation and refunds are covered by automated tests against "
    "python-telegram-bot's types but have not been exercised with real Stars. Mock mode "
    "exercises the same internal payment path end to end."
)
VERIFICATION_NOT_LIVE = (
    "Stars mode: not yet exercised against live Telegram. Invoice creation, pre-checkout, "
    "payment confirmation and refunds are covered by automated tests against "
    "python-telegram-bot's types. Mock mode exercises the same internal payment path end "
    "to end."
)

# Paths the README may name that do not exist in a fresh checkout. Everything else it
# names (in a link or in backticks) must exist in the repository.
RUNTIME_PATHS = {
    ".env": "created by the user by copying .env.example; never committed",
    "data/": "the runtime data directory; gitignored and created on first run",
    "data/shop.db": "the database, created on first run",
    "shop.db": "the database file name, created on first run",
    "heartbeat": "written by the sweep job at run time",
    "backups/": "created by the backup command at run time",
    "dist/": "build output; gitignored",
    "build/": "build output; gitignored",
}
# Targets GitHub resolves against the repository URL (the CI badge). They name no file in
# the checkout, so only the workflow file they point at is checked.
GITHUB_RELATIVE = "../../actions/workflows/"

PATH_EXTENSIONS = (".py", ".md", ".yml", ".yaml", ".toml", ".sql", ".json", ".txt", ".example")


def read(path: Path = README) -> str:
    return path.read_text(encoding="utf-8")


# --- the order lifecycle diagram ---------------------------------------------------------------


def readme_transitions(text: str) -> set[tuple[str, str]]:
    """The (from, to) pairs of the README's stateDiagram-v2 block, without the [*] start."""
    block = re.search(r"```mermaid\s+stateDiagram-v2\n(.*?)```", text, re.S)
    assert block, "the README has no stateDiagram-v2 block"
    pairs = set()
    for line in block.group(1).splitlines():
        match = re.fullmatch(r"\s*(\w+|\[\*\])\s*-->\s*(\w+|\[\*\])\s*", line)
        if match and "[*]" not in match.groups():
            pairs.add((match.group(1), match.group(2)))
    return pairs


def code_transitions() -> set[tuple[str, str]]:
    return {(src, dst) for src, targets in ALLOWED_TRANSITIONS.items() for dst in targets}


def test_the_readme_state_diagram_equals_allowed_transitions_in_both_directions():
    in_readme, in_code = readme_transitions(read()), code_transitions()
    assert in_code - in_readme == set(), "allowed in code but missing from the diagram"
    assert in_readme - in_code == set(), "drawn in the diagram but not allowed in code"


def test_the_diagram_starts_at_pending_payment():
    block = re.search(r"```mermaid\s+stateDiagram-v2\n(.*?)```", read(), re.S)
    assert block and "[*] --> pending_payment" in block.group(1)


@pytest.mark.parametrize("edit", ["remove", "add", "rename"])
def test_the_transition_check_is_not_vacuous_it_fails_on_an_edited_copy(tmp_path, edit):
    text = read()
    if edit == "remove":
        edited = text.replace("    delivered --> refunded\n", "", 1)
    elif edit == "add":
        edited = text.replace(
            "    delivered --> refunded\n", "    delivered --> refunded\n    refunded --> paid\n", 1
        )
    else:
        edited = text.replace("    paid --> delivered\n", "    paid --> deliverd\n", 1)
    assert edited != text
    copy = tmp_path / "README.md"
    copy.write_text(edited, encoding="utf-8")  # a temporary copy; the real README is untouched

    in_readme, in_code = readme_transitions(read(copy)), code_transitions()
    assert in_readme != in_code  # the same comparison the real test makes now fails
    assert readme_transitions(read()) == in_code  # while the real README still passes


# --- configuration ---------------------------------------------------------------------------


def settings_env_names() -> list[str]:
    return [name.upper() for name in Settings.model_fields]


def test_every_setting_is_in_the_readme_table_and_in_env_example():
    text = read()
    table = re.findall(r"^\|\s*`([A-Z_]+)`\s*\|", text, re.M)
    env_example = {
        line.split("=", 1)[0].strip()
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    }
    names = settings_env_names()
    assert {"BOT_TOKEN", "OWNER_ID", "PAYMENT_MODE", "DB_PATH", "LOG_LEVEL", "SEED_DEMO"} == set(
        names
    )
    for name in names:
        assert name in table, f"{name} is missing from the README configuration table"
        assert name in env_example, f"{name} is missing from .env.example"
    assert set(table) <= set(names), "the README table lists a variable Settings does not have"


# --- paths and links -------------------------------------------------------------------------


def _strip_fences(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


def heading_anchors(text: str) -> set[str]:
    anchors = set()
    for title in re.findall(r"^#{1,6}\s+(.*?)\s*$", _strip_fences(text), re.M):
        slug = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
        anchors.add(slug)
    return anchors


def _allowed_runtime(token: str) -> bool:
    for allowed in RUNTIME_PATHS:
        if token == allowed or token.rstrip("/") == allowed.rstrip("/"):
            return True
    return False


def path_problems(text: str, root: Path = ROOT) -> list[str]:
    """Markdown link targets and backticked repository paths that do not exist."""
    problems: list[str] = []
    prose = _strip_fences(text)

    for target in re.findall(r"\]\(([^)\s]+)\)", prose):
        if re.match(r"(https?:|mailto:)", target):
            continue
        if target.startswith("#"):
            if target[1:] not in heading_anchors(text):
                problems.append(f"broken anchor {target}")
            continue
        if target.startswith(GITHUB_RELATIVE):
            workflow = target[len(GITHUB_RELATIVE) :].split("/")[0]
            if not (root / ".github" / "workflows" / workflow).is_file():
                problems.append(f"no workflow file for {target}")
            continue
        path = target.split("#", 1)[0]
        if not _allowed_runtime(path) and not (root / path).exists():
            problems.append(f"missing link target {target}")

    for token in re.findall(r"`([^`\n]+)`", prose):
        if token.startswith("/"):
            continue  # a bot command (/admin) or an absolute path inside the container
        if not re.fullmatch(r"[\w./-]+", token):
            continue  # a command line, a placeholder like <db dir>, an expression
        if "/" not in token and not token.endswith(PATH_EXTENSIONS) and token != "Dockerfile":
            continue  # a plain word, an identifier or an option such as -wal
        if token.startswith(("-", ".venv")) or re.fullmatch(r"\.\w+", token) and token != ".env":
            continue  # an option, or a bare file extension such as .txt
        if _allowed_runtime(token) or (root / token).exists():
            continue
        problems.append(f"missing path {token}")
    return problems


WORKFLOW_REFERENCE = re.compile(r"actions/workflows/([^/\s)#?]+\.ya?ml)")


def workflow_problems(text: str, root: Path = ROOT) -> list[str]:
    """Every ".../actions/workflows/<file>.yml" the README mentions (the CI badge and its
    link are absolute URLs, which path_problems ignores) must name a file that exists
    in .github/workflows/."""
    return [
        f"no workflow file {name}"
        for name in WORKFLOW_REFERENCE.findall(text)
        if not (root / ".github" / "workflows" / name).is_file()
    ]


def test_every_workflow_the_readme_points_at_exists():
    assert len(WORKFLOW_REFERENCE.findall(read())) >= 2  # the badge and its link
    assert workflow_problems(read()) == []


def test_every_relative_path_and_link_in_the_readme_exists():
    assert path_problems(read()) == []


def test_the_allowlist_is_small_and_every_entry_is_explained():
    assert all(reason.strip() for reason in RUNTIME_PATHS.values())
    assert len(RUNTIME_PATHS) <= 10


def test_the_path_check_fails_on_a_typoed_repository_path_in_an_edited_copy(tmp_path):
    text = read()
    assert "`bot/concurrency.py`" in text and "(SECURITY.md)" in text
    typo_in_backticks = text.replace("`bot/concurrency.py`", "`bot/concurency.py`", 1)
    typo_in_link = text.replace("(SECURITY.md)", "(SECURITY.mdd)", 1)
    typo_in_workflow = text.replace("workflows/ci.yml", "workflows/cii.yml")
    typo_in_anchor = text.replace("(#payment-modes)", "(#payment-mode)", 1)
    for name, edited in {
        "backticks": typo_in_backticks,
        "link": typo_in_link,
        "workflow": typo_in_workflow,
        "anchor": typo_in_anchor,
    }.items():
        assert edited != text, name
        copy = tmp_path / f"README-{name}.md"
        copy.write_text(edited, encoding="utf-8")  # temporary copy only
        if name == "workflow":
            # the badge URLs are external, so path_problems ignores them by design;
            # workflow_problems is the check that must catch this typo
            assert workflow_problems(read(copy)), "typo in workflow was not detected"
            assert path_problems(read(copy)) == []
        else:
            assert path_problems(read(copy)), f"typo in {name} was not detected"
    assert path_problems(text) == []  # the real README is clean
    assert workflow_problems(text) == []


def test_allowlisted_runtime_paths_do_not_hide_a_typo_next_to_them(tmp_path):
    edited = read().replace("`data/shop.db`", "`dta/shop.db`", 1)
    copy = tmp_path / "README.md"
    copy.write_text(edited, encoding="utf-8")
    assert any("dta/shop.db" in p for p in path_problems(read(copy)))


# --- the Security section ----------------------------------------------------------------------


def security_section(text: str) -> str:
    match = re.search(r"^## Security design\n(.*?)^## ", text, re.S | re.M)
    assert match, "no Security design section"
    return match.group(1)


def test_every_test_file_the_security_section_names_exists():
    named = set(re.findall(r"`(tests/[\w./-]+\.py)`", security_section(read())))
    assert len(named) >= 10  # the section really cites its evidence
    missing = sorted(name for name in named if not (ROOT / name).is_file())
    assert missing == []


# --- verification status -----------------------------------------------------------------------


def test_the_readme_has_the_exact_verification_status_for_the_chosen_value():
    text = read()
    expected, other = (
        (VERIFICATION_LIVE, VERIFICATION_NOT_LIVE)
        if INVOICE_CHECKED_LIVE
        else (VERIFICATION_NOT_LIVE, VERIFICATION_LIVE)
    )
    section = re.search(r"^## Verification status\n+(.*?)\n+^## ", text, re.S | re.M)
    assert section and section.group(1).strip() == expected
    assert other not in text


# --- no counts that go stale ------------------------------------------------------------------


COUNT_CLAIM = re.compile(
    "|".join(
        [
            r"\b\d[\d,]*\+?\s+(?:automated\s+)?(?:unit\s+|integration\s+)?"
            r"(?:tests?|test cases|assertions)\b",
            r"\b(?:tests?|cases)\s*[:=]\s*\d+",
            r"\bcoverage\b[^\n]{0,40}\d+\s*%",
            r"\b\d+\s*%\s+(?:test\s+)?coverage",
        ]
    ),
    re.I,
)


@pytest.mark.parametrize("path", [README, CHANGELOG], ids=["README", "CHANGELOG"])
def test_no_hard_coded_test_counts_or_coverage_percentages(path):
    text = read(path)
    assert [m.group(0) for m in COUNT_CLAIM.finditer(text)] == []
    assert not re.search(r"\d\s*%", text), "a percentage (coverage?) is hard-coded"


def test_the_count_check_detects_what_it_is_for():
    for sample in ("1,600 tests pass", "over 900 tests", "coverage is 97%", "95% coverage"):
        assert COUNT_CLAIM.search(sample) or re.search(r"\d\s*%", sample), sample


# --- the licence ---------------------------------------------------------------------------------


def test_the_licence_is_mit_with_the_owners_holder_name():
    licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert licence.startswith("MIT License")
    assert "Copyright (c) 2026 NUMINAE" in licence
