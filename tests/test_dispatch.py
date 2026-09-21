"""Offline dispatch reject / upsert / telegram nonfatal paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from db import RecordResult, connect, list_active_positions, open_active_position
from dispatch import _reject_fund_or_news, try_dispatch_buy
from fundamentals import FundamentalsResult
from indicators import BarSnapshot, SetupFlags
from sentiment import MacroSentiment, NewsVelocityResult
from sizing import PositionSize
from thresholds import EXTREME_GREED_SCORE


@dataclass(frozen=True)
class _Cfg:
    telegram_bot_token: str = "tok"
    telegram_chat_id: str = "chat"
    portfolio_risk_cad: float = 200.0
    cooldown_days: int = 5
    signals_db: Path = Path("unused.db")


def _bar() -> BarSnapshot:
    return BarSnapshot(
        close=100.0,
        open=99.0,
        high=101.0,
        low=98.0,
        volume=2_000_000.0,
        sma_50=100.0,
        sma_150=95.0,
        sma_200=90.0,
        ema_20=99.5,
        rsi=45.0,
        atr=2.0,
        vol_sma=1_000_000.0,
        stock_roc=0.10,
        sma_50_slope=0.5,
        adx=30.0,
        addv=100_000_000.0,
        hist_vol=0.01,
    )


def _flags() -> SetupFlags:
    return SetupFlags(
        is_macro_bullish=True,
        is_macro_bearish=False,
        pullback_pct=1.0,
        pullback_below_pct=-1.0,
        is_in_pullback=True,
        is_at_resistance=False,
        dist_to_200_sma_pct=10.0,
        is_rs_leader=True,
        is_rs_laggard=False,
        is_bounce_confirmed=True,
        is_rejection_confirmed=False,
        rs_vs_xiu=5.0,
        rvol=1.5,
        is_slope_positive=True,
        is_slope_negative=False,
        is_volume_confirmed=True,
        is_support_intact=True,
        is_resistance_intact=False,
        is_trend_strong=True,
        is_bull_bulletproof=True,
        is_bear_bulletproof=False,
        clv=0.75,
    )


def _fund_ok() -> FundamentalsResult:
    return FundamentalsResult(
        earnings_conflict=False,
        debt_safe=True,
        fcf_positive=True,
        quality_ok=True,
        notes="ok",
        metadata_complete=True,
    )


def _news_ok() -> NewsVelocityResult:
    return NewsVelocityResult(headlines_clean=True, hit_count=0, notes="clean")


def test_reject_fund_or_news_extreme_greed() -> None:
    macro = MacroSentiment(
        score=EXTREME_GREED_SCORE + 1.0,
        rating="extreme greed",
        sentiment_points=0.0,
    )
    assert macro.is_extreme_greed is True
    reason = _reject_fund_or_news("RY.TO", _fund_ok(), _news_ok(), macro)
    assert reason is not None
    assert "Extreme Greed" in reason


def test_try_dispatch_buy_rejects_extreme_greed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = connect(tmp_path / "dispatch.db")
    macro = MacroSentiment(
        score=EXTREME_GREED_SCORE + 5.0,
        rating="greed",
        sentiment_points=0.0,
    )
    sent_calls: list[str] = []
    monkeypatch.setattr(
        "dispatch.send_html_message",
        lambda *a, **k: sent_calls.append("sent") or True,
    )
    ok = try_dispatch_buy(
        conn=conn,
        cfg=_Cfg(),  # type: ignore[arg-type]
        ticker="RY.TO",
        sector="Financials",
        bar=_bar(),
        flags=_flags(),
        fund=_fund_ok(),
        news=_news_ok(),
        macro=macro,
        vix_close=18.0,
        vix_mult=1.0,
        dynamic_risk_cad=200.0,
        alerted_sectors=set(),
    )
    assert ok is False
    assert list_active_positions(conn) == []
    assert sent_calls == []
    assert "Extreme Greed" in capsys.readouterr().out


def test_try_dispatch_buy_upsert_refuse_when_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = connect(tmp_path / "upsert.db")
    size = PositionSize(
        entry=100.0,
        atr=2.0,
        r=3.0,
        stop=97.0,
        target_1=104.5,
        shares=9,
        t1_shares=3,
        runner_shares=6,
        risk_cad=20.0,
        max_limit_price=100.3,
    )
    assert open_active_position(conn, ticker="RY.TO", size=size, sector="Financials")

    macro = MacroSentiment(score=40.0, rating="fear", sentiment_points=10.0)
    # Bypass cooldown / sizing by stubbing record and size.
    monkeypatch.setattr(
        "dispatch.size_position",
        lambda *_a, **_k: size,
    )
    monkeypatch.setattr(
        "dispatch.record_if_allowed",
        lambda *_a, **_k: RecordResult(inserted=True, reason="recorded"),
    )
    monkeypatch.setattr("dispatch.send_html_message", lambda *_a, **_k: True)

    ok = try_dispatch_buy(
        conn=conn,
        cfg=_Cfg(),  # type: ignore[arg-type]
        ticker="RY.TO",
        sector="Financials",
        bar=_bar(),
        flags=_flags(),
        fund=_fund_ok(),
        news=_news_ok(),
        macro=macro,
        vix_close=18.0,
        vix_mult=1.0,
        dynamic_risk_cad=200.0,
        alerted_sectors=set(),
    )
    assert ok is False
    assert "UPSERT REFUSED" in capsys.readouterr().out


def test_try_dispatch_buy_telegram_nonfatal_still_books(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = connect(tmp_path / "tg.db")
    size = PositionSize(
        entry=100.0,
        atr=2.0,
        r=3.0,
        stop=97.0,
        target_1=104.5,
        shares=9,
        t1_shares=3,
        runner_shares=6,
        risk_cad=20.0,
        max_limit_price=100.3,
    )
    macro = MacroSentiment(score=40.0, rating="fear", sentiment_points=10.0)
    monkeypatch.setattr("dispatch.size_position", lambda *_a, **_k: size)
    monkeypatch.setattr(
        "dispatch.record_if_allowed",
        lambda *_a, **_k: RecordResult(inserted=True, reason="recorded"),
    )
    monkeypatch.setattr("dispatch.send_html_message", lambda *_a, **_k: False)

    ok = try_dispatch_buy(
        conn=conn,
        cfg=_Cfg(),  # type: ignore[arg-type]
        ticker="TD.TO",
        sector="Financials",
        bar=_bar(),
        flags=_flags(),
        fund=_fund_ok(),
        news=_news_ok(),
        macro=macro,
        vix_close=18.0,
        vix_mult=1.0,
        dynamic_risk_cad=200.0,
        alerted_sectors=set(),
    )
    assert ok is True
    rows = list_active_positions(conn)
    assert len(rows) == 1
    assert rows[0].ticker == "TD.TO"
    assert "alert not delivered" in capsys.readouterr().out
