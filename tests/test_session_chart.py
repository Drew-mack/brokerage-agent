from datetime import date, time
from types import SimpleNamespace

from portfolio_agent.services.brief.session_chart import (
    _timestamp_ms,
    build_session_chart_svg,
)


class IncompleteHistoryClient:
    def get_price_history(self, symbol, **kwargs):
        if symbol == "AAPL":
            raise RuntimeError("no intraday history")

        return {
            "candles": [
                {"datetime": _timestamp_ms(date(2026, 9, 23), time(9, 30)), "close": 500},
                {"datetime": _timestamp_ms(date(2026, 9, 23), time(9, 35)), "close": 501},
            ],
            "previousClose": 499,
        }


def test_chart_survives_missing_position_history():
    analytics = SimpleNamespace(
        session_date=date(2026, 9, 23),
        estimated_previous_value=1000,
        positions=[
            SimpleNamespace(
                symbol="AAPL",
                previous_quantity=10,
                previous_value=1000,
                previous_close=100,
            )
        ],
    )

    chart = build_session_chart_svg(analytics, "VOO", IncompleteHistoryClient())

    assert chart is not None
    assert "Portfolio" in chart
    assert "VOO" in chart
