"""
The audit that gives the project a safety net.

Generates the defect-seeded fixture set into a temp folder, runs the real
checker over it, and asserts two things at once:

  * every seeded file fails *exactly* the checks its defect was designed to
    trigger -- no missed detections, and no unintended cascades
  * every other file is completely clean

Both directions matter. A check that stops detecting its defect is an
obvious regression; a check that starts firing on good data is a subtler one
that a "did anything fail?" smoke test would miss entirely.

This replaces the ad hoc script that was used to catch two real cascade bugs
during development and then thrown away.

    pytest                # round 1 (five months, covers every check)
    pytest -m slow        # all ten months
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Same flat-import convention the rest of the project uses (see CLAUDE.md).
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import generate_test_data  # noqa: E402
from checks import CHECK_NAMES, run_checks_for_table  # noqa: E402
from config_loader import load_table_configs  # noqa: E402
from defect_injector import DATAFRAME_DEFECTS  # noqa: E402
from generate_test_data import DEFECT_PLAN  # noqa: E402

CONFIG_PATH = PROJECT_ROOT / "config" / "tables_config.json"

# Round 1 seeds one instance of every defect type; round 2 repeats them on
# different tables. Round 1 alone therefore covers all 11 checks, which is
# why it's the default -- the full ten months is the same assertions again
# on other tables, so it's opt-in rather than paid for on every run.
ROUND_ONE = ["202603", "202604", "202605", "202606", "202607"]
ROUND_TWO = ["202608", "202609", "202610", "202611", "202612"]

# defect name -> the checks it is supposed to make non-passing.
EXPECTED_CHECKS: dict[str, set[str]] = {
    "missing_file": {"file_exists"},
    "wrong_sheet": {"sheet_exists"},
    **{name: checks for name, (_mutator, checks) in DATAFRAME_DEFECTS.items()},
}


def audit(months: list[str], root: Path) -> tuple[list[str], set[str]]:
    """
    Generate `months` into `root`, check them, and compare against the plan.

    Returns any mismatches found plus every check name the checker emitted
    (so a separate test can confirm CHECK_NAMES is complete).
    """
    configs = load_table_configs(str(CONFIG_PATH))
    generate_test_data.main(months, root=root)

    mismatches: list[str] = []
    emitted: set[str] = set()

    for yyyymm in months:
        for config in configs:
            results = run_checks_for_table(config, yyyymm, str(root))
            emitted.update(r.check_name for r in results)

            # Non-passing rather than failing: a seeded defect may raise a
            # warning (stale_source) rather than a hard failure.
            actual = {r.check_name for r in results if r.status != "PASS"}
            defect = DEFECT_PLAN.get((config["table_name"], yyyymm))
            expected = EXPECTED_CHECKS[defect] if defect else set()

            if actual != expected:
                mismatches.append(
                    f"{yyyymm} {config['table_name']}"
                    f"{f' (seeded {defect})' if defect else ' (should be clean)'}: "
                    f"expected {sorted(expected) or 'no findings'}, got {sorted(actual) or 'none'}"
                )

    return mismatches, emitted


@pytest.fixture(scope="module")
def round_one(tmp_path_factory):
    """Generate and audit round 1 once, then share it across tests."""
    return audit(ROUND_ONE, tmp_path_factory.mktemp("round_one"))


def test_seeded_defects_fail_exactly_their_intended_checks(round_one):
    mismatches, _ = round_one
    assert not mismatches, "\n".join(["Fixture audit found problems:", *mismatches])


def test_check_names_matches_what_the_checker_emits(round_one):
    """
    Guards the report against drift: a check that isn't in CHECK_NAMES loses
    its column, and a name in CHECK_NAMES that nothing emits is dead weight.
    """
    _, emitted = round_one
    assert emitted == set(CHECK_NAMES)


def test_check_names_has_no_duplicates():
    assert len(CHECK_NAMES) == len(set(CHECK_NAMES))


def test_round_one_covers_every_check():
    """If a new check is added, the fixtures must exercise its failure path."""
    covered = set()
    for (_table, yyyymm), defect in DEFECT_PLAN.items():
        if yyyymm in ROUND_ONE:
            covered |= EXPECTED_CHECKS[defect]

    assert covered == set(CHECK_NAMES), (
        "Round 1 no longer exercises every check -- seed a defect for: "
        f"{sorted(set(CHECK_NAMES) - covered)}"
    )


def test_every_planned_defect_is_a_known_defect_type():
    unknown = {d for d in DEFECT_PLAN.values() if d not in EXPECTED_CHECKS}
    assert not unknown, f"DEFECT_PLAN references unknown defect type(s): {sorted(unknown)}"


def test_defect_plan_references_real_tables():
    known = {c["table_name"] for c in load_table_configs(str(CONFIG_PATH))}
    planned = {table for table, _ in DEFECT_PLAN}
    assert planned <= known, f"DEFECT_PLAN references unknown table(s): {sorted(planned - known)}"


@pytest.mark.slow
def test_full_ten_month_audit(tmp_path):
    """The same assertions across both rounds, on every table."""
    mismatches, _ = audit(ROUND_ONE + ROUND_TWO, tmp_path)
    assert not mismatches, "\n".join(["Full fixture audit found problems:", *mismatches])
