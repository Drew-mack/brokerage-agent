from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from portfolio_agent.domain.portfolio import load_portfolio
from portfolio_agent.domain.transactions import load_transactions
from portfolio_agent.integrations.schwab import SchwabClient
from portfolio_agent.storage.dynamodb_storage import get_snapshot_at_or_before, save_snapshot
from portfolio_agent.storage.serialization import snapshot_to_portfolio

BENCHMARK_SYMBOL = "VOO"
MARKET_TIMEZONE = ZoneInfo("America/New_York")
MARKET_CLOSE_TIME = time(16, 0)
MARKET_DATA_SETTLE_BUFFER = timedelta(minutes=15)

SNAPSHOT_METHOD = "historical_snapshot"
ESTIMATE_METHOD = "current_holdings_estimate"


@dataclass
class AggregatedPosition:
    symbol: str
    asset_type: str
    quantity: float
    market_value: float
    account_count: int


@dataclass
class PositionPerformance:
    symbol: str
    quantity: float
    previous_quantity: float

    previous_close: float
    latest_close: float

    previous_value: float
    latest_value: float

    dollar_change: float
    return_pct: float

    portfolio_contribution: float
    current_weight: float

    account_count: int


@dataclass
class PortfolioAnalytics:
    previous_session_date: date
    session_date: date

    portfolio_value: float
    cash: float

    estimated_previous_value: float
    dollar_change: float
    portfolio_return: float

    benchmark_symbol: str
    benchmark_return: float
    relative_return: float

    largest_position_weight: float
    top_three_weight: float
    cash_weight: float

    performance_method: str
    historical_snapshot_available: bool
    historical_snapshot_timestamp: str | None

    trade_count: int
    transfer_count: int
    deposits: float
    withdrawals: float
    net_external_cash_flow: float
    dividends_and_interest: float
    other_transaction_count: int

    contributors: list[PositionPerformance] = field(default_factory=list)
    detractors: list[PositionPerformance] = field(default_factory=list)
    positions: list[PositionPerformance] = field(default_factory=list)
    missing_price_symbols: list[str] = field(default_factory=list)


def aggregate_positions(portfolio):
    """Combine identical symbols across portfolio accounts."""

    aggregated = {}

    for account in portfolio.accounts:
        symbols_seen_in_account = set()

        for position in account.positions:
            symbol = position.symbol

            if symbol not in aggregated:
                aggregated[symbol] = {
                    "symbol": symbol,
                    "asset_type": position.asset_type,
                    "quantity": 0.0,
                    "market_value": 0.0,
                    "account_count": 0,
                }

            aggregated[symbol]["quantity"] += position.quantity
            aggregated[symbol]["market_value"] += position.market_value

            if symbol not in symbols_seen_in_account:
                aggregated[symbol]["account_count"] += 1
                symbols_seen_in_account.add(symbol)

    return [
        AggregatedPosition(
            symbol=data["symbol"],
            asset_type=data["asset_type"],
            quantity=data["quantity"],
            market_value=data["market_value"],
            account_count=data["account_count"],
        )
        for data in aggregated.values()
    ]


def positions_by_symbol(positions):
    """Return aggregated positions indexed by symbol."""

    return {position.symbol: position for position in positions}


def _extract_candles(price_history):
    """Return valid daily candles in chronological order."""

    valid_candles = []

    for candle in price_history.get(
        "candles",
        [],
    ):
        close = candle.get("close")
        timestamp = candle.get("datetime")

        if close is None or timestamp is None:
            continue

        valid_candles.append(candle)

    valid_candles.sort(key=lambda candle: candle["datetime"])

    return valid_candles


def _timestamp_to_datetime(timestamp_ms):
    """Convert a millisecond timestamp to an aware UTC datetime."""

    return datetime.fromtimestamp(
        timestamp_ms / 1000,
        tz=timezone.utc,
    )


def _candle_session_date(candle):
    """Return the market date represented by a Schwab daily candle."""

    return _timestamp_to_datetime(candle["datetime"]).date()


def _latest_eligible_session_date(now=None):
    """Return the latest calendar date that may be treated as completed."""

    if now is None:
        now_et = datetime.now(MARKET_TIMEZONE)
    else:
        if now.tzinfo is None:
            now_et = now.replace(tzinfo=MARKET_TIMEZONE)
        else:
            now_et = now.astimezone(MARKET_TIMEZONE)

    market_close = datetime.combine(
        now_et.date(),
        MARKET_CLOSE_TIME,
        tzinfo=MARKET_TIMEZONE,
    )

    completion_time = market_close + MARKET_DATA_SETTLE_BUFFER

    if now_et >= completion_time:
        return now_et.date()

    return now_et.date() - timedelta(days=1)


def _get_completed_session_pair(
    price_history,
    now=None,
):
    """
    Return the previous close and latest completed-session close.

    A same-day daily candle is ignored until after the regular market
    session has closed and a short settlement buffer has elapsed.
    Weekends and market holidays naturally fall out because they do not
    have daily candles.
    """

    candles = _extract_candles(price_history)

    latest_eligible_date = _latest_eligible_session_date(now=now)

    completed_candles = [
        candle for candle in candles if _candle_session_date(candle) <= latest_eligible_date
    ]

    if len(completed_candles) < 2:
        raise ValueError("At least two completed daily candles are required.")

    previous_candle = completed_candles[-2]
    session_candle = completed_candles[-1]

    previous_session_date = _candle_session_date(previous_candle)

    session_date = _candle_session_date(session_candle)

    return (
        float(previous_candle["close"]),
        float(session_candle["close"]),
        previous_session_date,
        session_date,
    )


def _get_closes_for_sessions(
    price_history,
    previous_session_date,
    session_date,
):
    """Return closes for two explicitly requested market dates."""

    candles_by_date = {
        _candle_session_date(candle): candle for candle in _extract_candles(price_history)
    }

    previous_candle = candles_by_date.get(previous_session_date)

    session_candle = candles_by_date.get(session_date)

    if previous_candle is None:
        raise ValueError(f"No daily candle found for {previous_session_date}.")

    if session_candle is None:
        raise ValueError(f"No daily candle found for {session_date}.")

    return (
        float(previous_candle["close"]),
        float(session_candle["close"]),
    )


def _market_close_timestamp_ms(
    session_date,
):
    """Return 4:00 PM ET on a market date as milliseconds since epoch."""

    close_et = datetime.combine(
        session_date,
        MARKET_CLOSE_TIME,
        tzinfo=MARKET_TIMEZONE,
    )

    close_utc = close_et.astimezone(timezone.utc)

    return int(close_utc.timestamp() * 1000)


def _session_transaction_window(
    session_date,
):
    """Return the full reported market date as a UTC transaction window."""

    start_et = datetime.combine(
        session_date,
        time.min,
        tzinfo=MARKET_TIMEZONE,
    )

    end_et = datetime.combine(
        session_date,
        time.max,
        tzinfo=MARKET_TIMEZONE,
    )

    return (
        start_et.astimezone(timezone.utc),
        end_et.astimezone(timezone.utc),
    )


def _find_previous_market_snapshot(
    previous_market_timestamp,
):
    """Find a portfolio snapshot from the previous market date."""

    previous_date = (
        _timestamp_to_datetime(previous_market_timestamp).astimezone(MARKET_TIMEZONE).date()
    )

    end_of_day_et = datetime.combine(
        previous_date,
        time.max,
        tzinfo=MARKET_TIMEZONE,
    )

    snapshot = get_snapshot_at_or_before(end_of_day_et.astimezone(timezone.utc).isoformat())

    if snapshot is None:
        return None

    snapshot_time = datetime.fromisoformat(snapshot["timestamp"])

    if snapshot_time.tzinfo is None:
        snapshot_time = snapshot_time.replace(tzinfo=timezone.utc)

    snapshot_date = snapshot_time.astimezone(MARKET_TIMEZONE).date()

    if snapshot_date != previous_date:
        return None

    return snapshot


def _quantities_match(
    current_positions,
    historical_positions,
    tolerance=1e-8,
):
    """Return whether holdings match between historical and current states."""

    current_map = positions_by_symbol(current_positions)

    historical_map = positions_by_symbol(historical_positions)

    all_symbols = set(current_map) | set(historical_map)

    for symbol in all_symbols:
        current_quantity = current_map[symbol].quantity if symbol in current_map else 0.0

        historical_quantity = historical_map[symbol].quantity if symbol in historical_map else 0.0

        if abs(current_quantity - historical_quantity) > tolerance:
            return False

    return True


def analyze_position(
    current_position,
    previous_quantity,
    price_history,
    previous_session_date,
    session_date,
):
    """Calculate one position's close-to-close session performance."""

    (
        previous_close,
        session_close,
    ) = _get_closes_for_sessions(
        price_history=price_history,
        previous_session_date=(previous_session_date),
        session_date=session_date,
    )

    current_quantity = current_position.quantity

    previous_value = previous_quantity * previous_close

    # Use one quantity at both price endpoints so trades are not
    # mistaken for market gains or losses.
    session_value = previous_quantity * session_close

    dollar_change = session_value - previous_value

    if previous_close:
        return_pct = (session_close - previous_close) / previous_close
    else:
        return_pct = 0.0

    return PositionPerformance(
        symbol=current_position.symbol,
        quantity=current_quantity,
        previous_quantity=(previous_quantity),
        previous_close=previous_close,
        latest_close=session_close,
        previous_value=previous_value,
        latest_value=session_value,
        dollar_change=dollar_change,
        return_pct=return_pct,
        portfolio_contribution=0.0,
        current_weight=0.0,
        account_count=(current_position.account_count),
    )


def calculate_benchmark_return(
    client,
    benchmark_symbol=BENCHMARK_SYMBOL,
    now=None,
):
    """Calculate the benchmark return for the latest completed session."""

    history = client.get_daily_price_history(
        benchmark_symbol,
        period=1,
    )

    (
        previous_close,
        session_close,
        previous_session_date,
        session_date,
    ) = _get_completed_session_pair(
        price_history=history,
        now=now,
    )

    benchmark_return = (session_close - previous_close) / previous_close

    previous_timestamp = _market_close_timestamp_ms(previous_session_date)

    latest_timestamp = _market_close_timestamp_ms(session_date)

    return (
        benchmark_return,
        previous_timestamp,
        latest_timestamp,
    )


def _transaction_cash_change(
    transaction_summary,
):
    """
    Return the net cash movement represented by session transactions.

    Schwab's netAmount captures the cash side of trades, deposits,
    withdrawals, income, and other account activity. Security transfers
    generally have no cash effect.
    """

    return sum(transaction.net_amount for transaction in transaction_summary.transactions)


def analyze_portfolio(
    benchmark_symbol=BENCHMARK_SYMBOL,
    now=None,
):
    """
    Generate analytics for the latest completed regular market session.

    The benchmark defines the two market dates used throughout the run.
    Account value comes directly from Schwab balances. Historical closing
    prices are used only for completed-session performance calculations.
    """

    client = SchwabClient()
    portfolio = load_portfolio()
    # Persist the observation before calculating the next run's baseline.
    # DynamoDB uses the UTC timestamp generated by portfolio_to_snapshot as
    # the sort key, so every successful run becomes historical state.
    save_snapshot(portfolio)

    current_positions = aggregate_positions(portfolio)

    (
        benchmark_return,
        previous_timestamp,
        latest_timestamp,
    ) = calculate_benchmark_return(
        client=client,
        benchmark_symbol=(benchmark_symbol),
        now=now,
    )

    previous_session_date = (
        _timestamp_to_datetime(previous_timestamp).astimezone(MARKET_TIMEZONE).date()
    )

    session_date = _timestamp_to_datetime(latest_timestamp).astimezone(MARKET_TIMEZONE).date()

    snapshot = _find_previous_market_snapshot(previous_timestamp)

    historical_snapshot_available = snapshot is not None

    historical_snapshot_timestamp = snapshot["timestamp"] if snapshot is not None else None

    historical_portfolio = None
    historical_positions = None
    performance_method = ESTIMATE_METHOD

    if snapshot is not None:
        historical_portfolio = snapshot_to_portfolio(snapshot)

        historical_positions = aggregate_positions(historical_portfolio)

        if _quantities_match(
            current_positions,
            historical_positions,
        ):
            performance_method = SNAPSHOT_METHOD

    historical_map = {}

    if performance_method == SNAPSHOT_METHOD:
        historical_map = positions_by_symbol(historical_positions)

    (
        transaction_start,
        transaction_end,
    ) = _session_transaction_window(session_date)

    transaction_summary = load_transactions(
        start_datetime=(transaction_start),
        end_datetime=(transaction_end),
    )

    position_results = []
    missing_price_symbols = []

    for position in current_positions:
        symbol = position.symbol

        try:
            history = client.get_daily_price_history(
                symbol,
                period=1,
            )

            if performance_method == SNAPSHOT_METHOD and symbol in historical_map:
                previous_quantity = historical_map[symbol].quantity
            else:
                previous_quantity = position.quantity

            result = analyze_position(
                current_position=position,
                previous_quantity=(previous_quantity),
                price_history=history,
                previous_session_date=(previous_session_date),
                session_date=(session_date),
            )

            position_results.append(result)

        except Exception as error:
            missing_price_symbols.append(symbol)
            print(f"WARNING: Could not analyze {symbol}: {error}")

    previous_invested_value = sum(position.previous_value for position in position_results)

    session_invested_value = sum(position.latest_value for position in position_results)

    investment_change = sum(position.dollar_change for position in position_results)

    if performance_method == SNAPSHOT_METHOD and historical_portfolio is not None:
        previous_cash = historical_portfolio.cash

        session_cash = previous_cash + _transaction_cash_change(transaction_summary)
    else:
        # Without a suitable historical snapshot, exact historical cash
        # cannot be reconstructed safely. Current cash is used only as a
        # fallback while security values remain locked to session closes.
        previous_cash = portfolio.cash - _transaction_cash_change(transaction_summary)

        session_cash = portfolio.cash

    previous_portfolio_value = previous_invested_value + previous_cash

    session_portfolio_value = session_invested_value + session_cash

    if previous_portfolio_value:
        portfolio_return = investment_change / previous_portfolio_value
    else:
        portfolio_return = 0.0

    relative_return = portfolio_return - benchmark_return

    for position in position_results:
        if session_portfolio_value:
            position.portfolio_contribution = (
                position.dollar_change / previous_portfolio_value
                if previous_portfolio_value
                else 0.0
            )

            position.current_weight = position.latest_value / session_portfolio_value
        else:
            position.portfolio_contribution = 0.0
            position.current_weight = 0.0

    weights = sorted(
        [position.current_weight for position in position_results],
        reverse=True,
    )

    largest_position_weight = weights[0] if weights else 0.0

    top_three_weight = sum(weights[:3])

    if session_portfolio_value:
        cash_weight = session_cash / session_portfolio_value
    else:
        cash_weight = 0.0

    contributors = sorted(
        [position for position in position_results if position.dollar_change > 0],
        key=lambda position: (position.dollar_change),
        reverse=True,
    )

    detractors = sorted(
        [position for position in position_results if position.dollar_change < 0],
        key=lambda position: (position.dollar_change),
    )

    analytics = PortfolioAnalytics(
        previous_session_date=(previous_session_date),
        session_date=(session_date),
        portfolio_value=portfolio.total_value,
        cash=session_cash,
        estimated_previous_value=(previous_portfolio_value),
        dollar_change=(investment_change),
        portfolio_return=(portfolio_return),
        benchmark_symbol=(benchmark_symbol),
        benchmark_return=(benchmark_return),
        relative_return=(relative_return),
        largest_position_weight=(largest_position_weight),
        top_three_weight=(top_three_weight),
        cash_weight=(cash_weight),
        performance_method=(performance_method),
        historical_snapshot_available=(historical_snapshot_available),
        historical_snapshot_timestamp=(historical_snapshot_timestamp),
        trade_count=len(transaction_summary.trades),
        transfer_count=len(transaction_summary.transfers),
        deposits=(transaction_summary.deposits),
        withdrawals=(transaction_summary.withdrawals),
        net_external_cash_flow=(transaction_summary.net_external_cash_flow),
        dividends_and_interest=(transaction_summary.dividends_and_interest),
        other_transaction_count=len(transaction_summary.other),
        contributors=contributors,
        detractors=detractors,
        positions=position_results,
        missing_price_symbols=missing_price_symbols,
    )

    return (
        analytics,
        previous_timestamp,
        latest_timestamp,
    )
