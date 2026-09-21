"""Offline portfolio heat / free-roll math."""

from __future__ import annotations

from dataclasses import dataclass

from sizing import MAX_PORTFOLIO_HEAT_R, evaluate_heat_veto, portfolio_heat_r


@dataclass
class FakePos:
    entry_price: float
    current_stop: float
    shares_remaining: int


def test_max_heat_constant() -> None:
    assert MAX_PORTFOLIO_HEAT_R == 6.0


def test_open_risk_sums_in_r() -> None:
    # Entry 100, stop 97 → $3/sh risk; 10 sh → $30; unit $10 → 3.0R
    positions = [FakePos(100.0, 97.0, 10)]
    assert portfolio_heat_r(positions, unit_risk_cad=10.0) == 3.0


def test_free_roll_when_stop_at_or_above_entry() -> None:
    # After BE ratchet, stop >= entry → 0 open risk
    free = [FakePos(100.0, 100.0, 6), FakePos(50.0, 51.0, 20)]
    assert portfolio_heat_r(free, unit_risk_cad=10.0) == 0.0


def test_mixed_book_heat() -> None:
    positions = [
        FakePos(100.0, 97.0, 10),  # 3.0R at unit=10
        FakePos(100.0, 100.2, 6),  # free roll
        FakePos(80.0, 78.0, 5),  # 1.0R
    ]
    heat = portfolio_heat_r(positions, unit_risk_cad=10.0)
    assert heat == 4.0
    assert heat < MAX_PORTFOLIO_HEAT_R


def test_heat_veto_threshold() -> None:
    # Six full $10 risks at unit=$10 → 6.0R exactly → veto boundary
    positions = [FakePos(100.0, 99.0, 10) for _ in range(6)]  # $10 each → 1R × 6
    heat = portfolio_heat_r(positions, unit_risk_cad=10.0)
    assert heat == MAX_PORTFOLIO_HEAT_R
    assert heat >= MAX_PORTFOLIO_HEAT_R


def test_zero_unit_risk_returns_zero() -> None:
    assert portfolio_heat_r([FakePos(100.0, 97.0, 10)], unit_risk_cad=0.0) == 0.0


def test_heat_veto_decision_and_log_lines() -> None:
    flat = evaluate_heat_veto([], unit_risk_cad=10.0)
    assert flat.veto is False
    assert flat.log_line is None

    open_book = evaluate_heat_veto(
        [FakePos(100.0, 97.0, 10), FakePos(80.0, 78.0, 5)],
        unit_risk_cad=10.0,
    )
    assert open_book.open_r == 4.0
    assert open_book.veto is False
    assert open_book.log_line == " -> [HEAT] Open portfolio risk: 4.0R / 6R"

    capped = evaluate_heat_veto(
        [FakePos(100.0, 99.0, 10) for _ in range(6)],
        unit_risk_cad=10.0,
    )
    assert capped.open_r == MAX_PORTFOLIO_HEAT_R
    assert capped.veto is True
    assert capped.log_line == (
        " -> [HEAT VETO] Portfolio at 6.0R open risk. New buys blocked."
    )
