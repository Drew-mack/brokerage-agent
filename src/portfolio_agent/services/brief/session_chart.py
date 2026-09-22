"""Render a compact portfolio-versus-benchmark session chart for email."""

from dataclasses import dataclass
from datetime import datetime, time, timezone
from html import escape
from zoneinfo import ZoneInfo

from portfolio_agent.integrations.schwab import SchwabClient


MARKET_TIMEZONE = ZoneInfo("America/New_York")
CHART_WIDTH = 600
CHART_HEIGHT = 190
PLOT_LEFT = 40
PLOT_RIGHT = 574
PLOT_TOP = 24
PLOT_BOTTOM = 150


@dataclass(frozen=True)
class ChartPoint:
    timestamp: int
    portfolio_return: float
    benchmark_return: float


def _timestamp_ms(session_date, market_time):
    value = datetime.combine(
        session_date,
        market_time,
        tzinfo=MARKET_TIMEZONE,
    )
    return int(value.astimezone(timezone.utc).timestamp() * 1000)


def _candles_by_timestamp(history):
    return {
        int(candle["datetime"]): float(candle["close"])
        for candle in history.get("candles", [])
        if candle.get("datetime") is not None and candle.get("close") is not None
    }


def _value_at_or_before(candles, timestamp):
    eligible = [item for item in candles.items() if item[0] <= timestamp]

    if not eligible:
        return None

    return max(eligible, key=lambda item: item[0])[1]


def _path(points, field, minimum, maximum):
    if not points:
        return ""

    timestamp_start = points[0].timestamp
    timestamp_end = points[-1].timestamp
    timestamp_range = max(timestamp_end - timestamp_start, 1)
    value_range = max(maximum - minimum, 0.0001)

    coordinates = []

    for point in points:
        x = PLOT_LEFT + (
            (point.timestamp - timestamp_start) / timestamp_range
        ) * (PLOT_RIGHT - PLOT_LEFT)
        y = PLOT_BOTTOM - (
            (getattr(point, field) - minimum) / value_range
        ) * (PLOT_BOTTOM - PLOT_TOP)
        coordinates.append(f"{x:.1f},{y:.1f}")

    return "M" + " L".join(coordinates)


def _label(value):
    return f"{value:+.2%}"


def build_session_chart_svg(analytics, benchmark_symbol, client=None):
    """Build an inline SVG chart from completed-session 5-minute candles.

    The chart uses prior-session quantities and holds cash constant. This
    makes the visual describe market movement rather than confusing intraday
    trades or transfers with investment performance.
    """

    client = client or SchwabClient()
    symbols = [
        position.symbol
        for position in analytics.positions
        if position.previous_quantity
    ]

    if not symbols:
        return None

    start_date = analytics.session_date
    start_timestamp = _timestamp_ms(start_date, time(9, 30))
    end_timestamp = _timestamp_ms(start_date, time(16, 0))

    histories = {}

    try:
        for symbol in [*symbols, benchmark_symbol]:
            histories[symbol] = client.get_price_history(
                symbol=symbol,
                frequency_type="minute",
                frequency=5,
                start_date=start_timestamp,
                end_date=end_timestamp,
                need_extended_hours_data=False,
                need_previous_close=True,
            )
    except Exception:
        return None

    candle_maps = {
        symbol: _candles_by_timestamp(history)
        for symbol, history in histories.items()
    }

    benchmark_candles = candle_maps.get(benchmark_symbol, {})
    benchmark_previous_close = next(
        (
            position.previous_close
            for position in analytics.positions
            if position.symbol == benchmark_symbol
        ),
        None,
    )

    if benchmark_previous_close is None:
        try:
            benchmark_history = client.get_daily_price_history(benchmark_symbol, period=1)
            daily_candles = sorted(
                benchmark_history.get("candles", []),
                key=lambda candle: candle.get("datetime", 0),
            )
            benchmark_previous_close = float(daily_candles[-2]["close"])
        except Exception:
            return None

    portfolio_previous_value = analytics.estimated_previous_value
    position_previous_values = {
        position.symbol: position.previous_value
        for position in analytics.positions
    }
    cash_previous = portfolio_previous_value - sum(position_previous_values.values())

    timestamps = sorted(
        timestamp
        for timestamp in benchmark_candles
        if start_timestamp <= timestamp <= end_timestamp
    )

    points = []

    for timestamp in timestamps:
        benchmark_price = _value_at_or_before(benchmark_candles, timestamp)

        if benchmark_price is None or not benchmark_previous_close:
            continue

        portfolio_value = cash_previous
        complete = True

        for position in analytics.positions:
            if not position.previous_quantity:
                continue

            price = _value_at_or_before(candle_maps.get(position.symbol, {}), timestamp)

            if price is None:
                complete = False
                break

            portfolio_value += position.previous_quantity * price

        if complete and portfolio_previous_value:
            points.append(
                ChartPoint(
                    timestamp=timestamp,
                    portfolio_return=(portfolio_value / portfolio_previous_value) - 1,
                    benchmark_return=(benchmark_price / benchmark_previous_close) - 1,
                )
            )

    if len(points) < 2:
        return None

    values = [
        value
        for point in points
        for value in (point.portfolio_return, point.benchmark_return)
    ]
    minimum = min(min(values), 0.0)
    maximum = max(max(values), 0.0)
    padding = max((maximum - minimum) * 0.16, 0.0015)
    minimum -= padding
    maximum += padding

    zero_y = PLOT_BOTTOM - ((0 - minimum) / (maximum - minimum)) * (
        PLOT_BOTTOM - PLOT_TOP
    )
    portfolio_final = points[-1].portfolio_return
    benchmark_final = points[-1].benchmark_return

    return f"""
      <div style="margin-top:30px;">
        <div style="font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:#7982a9;font-weight:650;">Market session</div>
        <div style="margin-top:12px;padding:18px 18px 14px;background:#1f2335;border-radius:8px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <div style="font-size:13px;color:#c0caf5;font-weight:550;">Intraday performance</div>
            <div style="font-size:11px;color:#7982a9;">Normalized to prior close</div>
          </div>
          <svg viewBox="0 0 {CHART_WIDTH} {CHART_HEIGHT}" width="100%" role="img" aria-label="Portfolio versus {escape(benchmark_symbol)} intraday performance">
            <line x1="{PLOT_LEFT}" y1="{zero_y:.1f}" x2="{PLOT_RIGHT}" y2="{zero_y:.1f}" stroke="#414868" stroke-width="1"/>
            <line x1="{PLOT_LEFT}" y1="{PLOT_TOP}" x2="{PLOT_RIGHT}" y2="{PLOT_TOP}" stroke="#414868" stroke-width="1" opacity=".38"/>
            <line x1="{PLOT_LEFT}" y1="{PLOT_BOTTOM}" x2="{PLOT_RIGHT}" y2="{PLOT_BOTTOM}" stroke="#414868" stroke-width="1" opacity=".38"/>
            <text x="2" y="{PLOT_TOP + 4}" fill="#7982a9" font-size="11">{_label(maximum)}</text>
            <text x="12" y="{zero_y + 4:.1f}" fill="#7982a9" font-size="11">0%</text>
            <text x="2" y="{PLOT_BOTTOM + 4}" fill="#7982a9" font-size="11">{_label(minimum)}</text>
            <text x="{PLOT_LEFT}" y="178" fill="#7982a9" font-size="11">9:30</text>
            <text x="307" y="178" fill="#7982a9" font-size="11" text-anchor="middle">12:00</text>
            <text x="{PLOT_RIGHT}" y="178" fill="#7982a9" font-size="11" text-anchor="end">4:00</text>
            <path d="{_path(points, 'portfolio_return', minimum, maximum)}" fill="none" stroke="#9ece6a" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="{_path(points, 'benchmark_return', minimum, maximum)}" fill="none" stroke="#7dcfff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
            <text x="{PLOT_RIGHT - 6}" y="28" fill="#7dcfff" font-size="11" text-anchor="end">{escape(benchmark_symbol)} {_label(benchmark_final)}</text>
            <text x="{PLOT_RIGHT - 6}" y="48" fill="#9ece6a" font-size="11" text-anchor="end">Portfolio {_label(portfolio_final)}</text>
          </svg>
          <div style="display:flex;gap:18px;margin-top:2px;font-size:11px;color:#a9b1d6;">
            <span><span style="color:#9ece6a;font-size:16px;vertical-align:-1px;">●</span>&nbsp; Portfolio</span>
            <span><span style="color:#7dcfff;font-size:16px;vertical-align:-1px;">●</span>&nbsp; {escape(benchmark_symbol)}</span>
          </div>
        </div>
      </div>
    """
