from datetime import date
from types import SimpleNamespace

from portfolio_agent.services.brief.email_renderer import EmailRenderer


def test_renderer_includes_tokyo_night_chart_and_changes_section():
    brief = SimpleNamespace(
        date=date(2026, 9, 23),
        session_date=date(2026, 9, 22),
        portfolio_value=19_943.23,
        dollar_change=27.31,
        portfolio_return=0.0014,
        benchmark_symbol="VOO",
        benchmark_return=0.0,
        relative_return=0.0014,
        changes=[
            SimpleNamespace(
                symbol="NVDA",
                return_pct=0.021,
                dollar_impact=18.42,
                explanation="Demand remained firm.",
                thesis_implication="The long-term thesis remains intact.",
                supporting_articles=[],
            )
        ],
        advice=[],
        session_chart_svg='<svg aria-label="session chart"></svg>',
    )

    html = EmailRenderer().render(brief)

    assert 'background:#1a1b26' in html
    assert "Tokyo Night" not in html
    assert "Changes" in html
    assert 'aria-label="session chart"' in html
    assert "NVDA" in html
