from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from portfolio_agent.domain import analytics as module
from portfolio_agent.domain.portfolio import Account, Portfolio, Position
from portfolio_agent.domain.transactions import TransactionSummary
from portfolio_agent.services.brief.email_renderer import EmailRenderer
from portfolio_agent.services.brief.session_chart import build_session_chart_svg


@pytest.mark.parametrize("missing", [False, True])
def test_account_balance_is_independent_of_session_prices(monkeypatch, missing):
    stock = Position("NVDA", "EQUITY", 10, 100, 1500)
    fund = Position("SWISX", "MUTUAL_FUND", 100, 20, 2750)
    portfolio = Portfolio(4250.02, 0.02, [
        Account("CASH", 1500, 0, [stock]),
        Account("CASH", 2750.02, 0.02, [fund]),
    ], [stock, fund])
    previous, latest = date(2026, 9, 24), date(2026, 9, 25)
    def history(symbol, **kwargs):
        if missing and symbol == "SWISX":
            return {"candles": []}
        return {"candles": [
            {"datetime": module._market_close_timestamp_ms(previous), "close": 10},
            {"datetime": module._market_close_timestamp_ms(latest), "close": 11},
        ]}
    client = Mock()
    client.get_daily_price_history.side_effect = history
    monkeypatch.setattr(module, "SchwabClient", lambda: client)
    monkeypatch.setattr(module, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(module, "save_snapshot", Mock())
    monkeypatch.setattr(module, "_find_previous_market_snapshot", lambda _: None)
    monkeypatch.setattr(module, "calculate_benchmark_return", lambda **kw: (
        0.01, module._market_close_timestamp_ms(previous),
        module._market_close_timestamp_ms(latest),
    ))
    monkeypatch.setattr(module, "load_transactions", lambda **kw: TransactionSummary("", ""))
    result, _, _ = module.analyze_portfolio()
    assert result.portfolio_value == 4250.02
    assert result.missing_price_symbols == (["SWISX"] if missing else [])
    brief = SimpleNamespace(**vars(result), date=date(2026, 9, 28), changes=[], advice=[])
    html = EmailRenderer().render(brief)
    assert "$4,250.02" in html
    if missing:
        assert "Session performance unavailable: missing prices for SWISX" in html
        chart_client = Mock()
        assert build_session_chart_svg(result, "VOO", chart_client) is None
        chart_client.get_intraday_price_history.assert_not_called()
