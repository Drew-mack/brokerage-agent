import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from portfolio_agent.config import settings
from portfolio_agent.domain.analytics import analyze_portfolio
from portfolio_agent.integrations.mailer.ses_sender import SESEmailSender
from portfolio_agent.services.brief.email_renderer import EmailRenderer
from portfolio_agent.services.brief.morning_brief import MorningBriefBuilder
from portfolio_agent.services.brief.session_chart import build_session_chart_svg
from portfolio_agent.services.research.finnhub_news import (
    FinnhubError,
    FinnhubNewsClient,
    NewsArticle,
)
from portfolio_agent.services.research.forward_analyzer import (
    ForwardAnalysis,
    ForwardAnalyzer,
    ForwardAnalyzerError,
)
from portfolio_agent.services.research.portfolio_advisor import (
    PortfolioAdvisor,
    PortfolioAdvisorError,
    PositionAdvice,
)
from portfolio_agent.services.research.ranking import (
    rank_company_news,
    rank_forward_news,
)
from portfolio_agent.services.research.research_analyzer import (
    MovementAnalysis,
    ResearchAnalyzer,
    ResearchAnalyzerError,
)
from portfolio_agent.storage.delivery_storage import (
    DeliveryAlreadyClaimed,
    claim_delivery,
    get_last_delivered_session,
    record_delivery,
)
from portfolio_agent.storage.thesis_storage import (
    ThesisStorageError,
    get_latest_thesis,
    save_thesis,
)

logger = logging.getLogger(__name__)


MIN_ABSOLUTE_RETURN = 0.02
MIN_PORTFOLIO_CONTRIBUTION = 0.0025
MAX_MOVEMENT_POSITIONS = 3
MAX_MOVEMENT_CANDIDATES = 10

MIN_FORWARD_PORTFOLIO_WEIGHT = 0.01
MAX_FORWARD_POSITIONS = 3
FORWARD_LOOKBACK_DAYS = 7
MAX_FORWARD_CANDIDATES = 12

BENCHMARK_SYMBOL = settings.benchmark_symbol

EXCLUDED_SYMBOLS = {
    "SPY",
    "VOO",
    "SWISX",
}


@dataclass
class PositionResearch:
    symbol: str
    return_pct: float
    dollar_change: float
    portfolio_contribution: float
    analysis: MovementAnalysis
    supporting_articles: list[NewsArticle]


@dataclass
class ForwardPositionResearch:
    symbol: str
    portfolio_weight: float
    return_pct: float
    portfolio_contribution: float
    analysis: ForwardAnalysis
    supporting_articles: list[NewsArticle]
    advice: PositionAdvice


class ResearchService:
    """Coordinates portfolio analytics, research, and investment advice."""

    def __init__(
        self,
        news_client: FinnhubNewsClient | None = None,
        movement_analyzer: ResearchAnalyzer | None = None,
        forward_analyzer: ForwardAnalyzer | None = None,
        portfolio_advisor: PortfolioAdvisor | None = None,
    ):
        self.news_client = news_client or FinnhubNewsClient()

        self.movement_analyzer = movement_analyzer or ResearchAnalyzer()

        self.forward_analyzer = forward_analyzer or ForwardAnalyzer()

        self.portfolio_advisor = portfolio_advisor or PortfolioAdvisor()

    def research_portfolio_movements(
        self,
        analytics,
        previous_timestamp: int,
        latest_timestamp: int,
    ) -> list[PositionResearch]:
        """Research important movements from the latest market session."""

        positions = self._select_movement_positions(analytics.positions)

        if not positions:
            return []

        move_start = self._timestamp_to_datetime(previous_timestamp)

        move_end = self._timestamp_to_datetime(latest_timestamp)

        start_date = analytics.previous_session_date
        end_date = analytics.session_date

        results = []

        for position in positions:
            try:
                result = self._research_movement_position(
                    symbol=position.symbol,
                    return_pct=(position.return_pct),
                    dollar_change=(position.dollar_change),
                    portfolio_contribution=(position.portfolio_contribution),
                    start_date=start_date,
                    end_date=end_date,
                    move_start=move_start,
                    move_end=move_end,
                )

                results.append(result)

            except (
                FinnhubError,
                ResearchAnalyzerError,
            ) as error:
                print(f"Movement research failed for {position.symbol}: {error}")

        return results

    def research_portfolio_forward(
        self,
        analytics,
        movement_results: list[PositionResearch] | None = None,
        as_of_date: date | None = None,
    ) -> list[ForwardPositionResearch]:
        """Research outlooks and advise on important holdings."""

        positions = self._select_forward_positions(analytics.positions)

        if not positions:
            return []

        movement_by_symbol = {result.symbol.upper(): result for result in (movement_results or [])}

        end_date = as_of_date or date.today()

        start_date = end_date - timedelta(days=FORWARD_LOOKBACK_DAYS)

        results = []

        for position in positions:
            try:
                movement_research = movement_by_symbol.get(position.symbol.upper())

                result = self._research_forward_position(
                    analytics=analytics,
                    position=position,
                    movement_research=(movement_research),
                    start_date=start_date,
                    end_date=end_date,
                )

                results.append(result)

            except (
                FinnhubError,
                ForwardAnalyzerError,
                PortfolioAdvisorError,
                ThesisStorageError,
            ) as error:
                print(f"Forward research failed for {position.symbol}: {error}")

        return results

    def _research_movement_position(
        self,
        symbol: str,
        return_pct: float,
        dollar_change: float,
        portfolio_contribution: float,
        start_date: date,
        end_date: date,
        move_start: datetime,
        move_end: datetime,
    ) -> PositionResearch:
        """Run movement research for one security."""

        articles = self.news_client.get_company_news(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )

        if not articles:
            raise FinnhubError(f"No Finnhub news was found for {symbol}.")

        ranked_articles = rank_company_news(
            articles=articles,
            symbol=symbol,
            move_start=move_start,
            move_end=move_end,
            limit=MAX_MOVEMENT_CANDIDATES,
        )

        if not ranked_articles:
            raise FinnhubError(f"No movement research candidates remained for {symbol}.")

        analysis = self.movement_analyzer.analyze_movement(
            symbol=symbol,
            return_pct=(return_pct * 100),
            articles=ranked_articles,
        )

        supporting_articles = self._resolve_articles(
            ranked_articles=(ranked_articles),
            article_ids=(analysis.supporting_article_ids),
        )

        return PositionResearch(
            symbol=symbol,
            return_pct=return_pct,
            dollar_change=dollar_change,
            portfolio_contribution=(portfolio_contribution),
            analysis=analysis,
            supporting_articles=(supporting_articles),
        )

    def _research_forward_position(
        self,
        analytics,
        position,
        movement_research: PositionResearch | None,
        start_date: date,
        end_date: date,
    ) -> ForwardPositionResearch:
        """Update the thesis and generate advice for one position."""

        symbol = position.symbol

        previous_thesis = get_latest_thesis(symbol)

        articles = self.news_client.get_company_news(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )

        if not articles:
            raise FinnhubError(f"No Finnhub news was found for {symbol}.")

        ranked_articles = rank_forward_news(
            articles=articles,
            symbol=symbol,
            limit=MAX_FORWARD_CANDIDATES,
        )

        if not ranked_articles:
            raise FinnhubError(f"No forward research candidates remained for {symbol}.")

        analysis = self.forward_analyzer.analyze(
            symbol=symbol,
            articles=ranked_articles,
            previous_thesis=(previous_thesis),
        )

        supporting_articles = self._resolve_articles(
            ranked_articles=(ranked_articles),
            article_ids=(analysis.supporting_article_ids),
        )

        if analysis.change_type != "unchanged":
            save_thesis(
                symbol=symbol,
                analysis=analysis,
                supporting_articles=(supporting_articles),
            )

        movement_explanation = None

        if movement_research is not None:
            movement_explanation = movement_research.analysis.explanation

        advice = self.portfolio_advisor.advise(
            symbol=symbol,
            portfolio_value=(analytics.portfolio_value),
            cash=analytics.cash,
            positions=analytics.positions,
            position=position,
            thesis=analysis,
            movement_explanation=(movement_explanation),
        )

        return ForwardPositionResearch(
            symbol=symbol,
            portfolio_weight=(position.current_weight),
            return_pct=(position.return_pct),
            portfolio_contribution=(position.portfolio_contribution),
            analysis=analysis,
            supporting_articles=(supporting_articles),
            advice=advice,
        )

    @staticmethod
    def _select_movement_positions(
        positions,
    ):
        """Choose portfolio movements worth researching."""

        qualifying_positions = []

        for position in positions:
            if position.symbol.upper() in EXCLUDED_SYMBOLS:
                continue

            meaningful_return = abs(position.return_pct) >= MIN_ABSOLUTE_RETURN

            meaningful_contribution = (
                abs(position.portfolio_contribution) >= MIN_PORTFOLIO_CONTRIBUTION
            )

            if meaningful_return or meaningful_contribution:
                qualifying_positions.append(position)

        qualifying_positions.sort(
            key=lambda position: (
                abs(position.portfolio_contribution),
                abs(position.return_pct),
            ),
            reverse=True,
        )

        return qualifying_positions[:MAX_MOVEMENT_POSITIONS]

    @staticmethod
    def _select_forward_positions(
        positions,
    ):
        """Choose meaningful individual stocks for forward research."""

        qualifying_positions = []

        for position in positions:
            if position.symbol.upper() in EXCLUDED_SYMBOLS:
                continue

            if position.current_weight < MIN_FORWARD_PORTFOLIO_WEIGHT:
                continue

            qualifying_positions.append(position)

        qualifying_positions.sort(
            key=lambda position: (position.current_weight),
            reverse=True,
        )

        return qualifying_positions[:MAX_FORWARD_POSITIONS]

    @staticmethod
    def _resolve_articles(
        ranked_articles,
        article_ids: list[int],
    ) -> list[NewsArticle]:
        """Map LLM article IDs back to their original news objects."""

        supporting_articles = []

        for article_id in article_ids:
            index = article_id - 1

            if 0 <= index < len(ranked_articles):
                supporting_articles.append(ranked_articles[index].article)

        return supporting_articles

    @staticmethod
    def _timestamp_to_datetime(
        timestamp_ms: int,
    ) -> datetime:
        """Convert a Schwab millisecond timestamp to UTC."""

        return datetime.fromtimestamp(
            timestamp_ms / 1000,
            tz=timezone.utc,
        )


def running_in_lambda():
    """Return whether execution is occurring inside AWS Lambda."""

    return bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME"))


def run_morning_brief(
    send_email: bool = True,
    write_preview: bool | None = None,
    prevent_duplicate_delivery: bool = False,
):
    """Run the complete portfolio morning-brief pipeline."""

    if write_preview is None:
        write_preview = not running_in_lambda()

    logger.info("Generating portfolio analytics")

    (
        analytics,
        previous_timestamp,
        latest_timestamp,
    ) = analyze_portfolio(benchmark_symbol=(BENCHMARK_SYMBOL))

    logger.info(
        "Portfolio analytics generated",
        extra={"session_date": str(analytics.session_date)},
    )

    session_date = analytics.session_date.isoformat()

    if send_email and prevent_duplicate_delivery:
        last_delivered_session = get_last_delivered_session()

        if last_delivered_session == session_date:
            logger.info("Morning Brief already delivered", extra={"session_date": session_date})

            return {
                "session_date": session_date,
                "portfolio_value": (analytics.portfolio_value),
                "portfolio_return": (analytics.portfolio_return),
                "message_id": None,
                "delivery_skipped": True,
            }

        try:
            claim_delivery(session_date)
        except DeliveryAlreadyClaimed:
            return {
                "session_date": session_date,
                "portfolio_value": analytics.portfolio_value,
                "portfolio_return": analytics.portfolio_return,
                "message_id": None,
                "delivery_skipped": True,
            }

    service = ResearchService()

    logger.info("Researching meaningful portfolio movements")

    movement_results = service.research_portfolio_movements(
        analytics=analytics,
        previous_timestamp=(previous_timestamp),
        latest_timestamp=(latest_timestamp),
    )

    logger.info("Movement research completed", extra={"count": len(movement_results)})

    forward_results = service.research_portfolio_forward(
        analytics=analytics,
        movement_results=movement_results,
    )
    logger.info("Forward research completed", extra={"count": len(forward_results)})

    brief_builder = MorningBriefBuilder()

    session_chart_svg = build_session_chart_svg(
        analytics=analytics,
        benchmark_symbol=analytics.benchmark_symbol,
    )

    morning_brief = brief_builder.build(
        analytics=analytics,
        movement_results=(movement_results),
        forward_results=(forward_results),
        session_chart_svg=session_chart_svg,
    )

    email_renderer = EmailRenderer()

    email_html = email_renderer.render(morning_brief)

    if write_preview:
        email_path = email_renderer.write(morning_brief)

        logger.info("HTML morning brief written", extra={"path": str(email_path)})

    message_id = None

    if send_email:
        sender_email = settings.sender_email
        recipient_email = settings.recipient_email
        aws_region = settings.aws_region

        aws_profile = None

        if not running_in_lambda():
            aws_profile = settings.aws_profile

        email_sender = SESEmailSender(
            sender_email=(sender_email),
            recipient_email=(recipient_email),
            aws_region=(aws_region),
            aws_profile=(aws_profile),
        )

        email_subject = (
            f"Andrew's Morning Portfolio Brief — "
            f"{morning_brief.date.strftime('%b')} {morning_brief.date.day}"
        )

        message_id = email_sender.send(
            subject=(email_subject),
            html=email_html,
        )

        logger.info("Morning Brief email sent", extra={"message_id": message_id})

        if prevent_duplicate_delivery:
            record_delivery(
                session_date=(session_date),
                message_id=(message_id),
            )

            logger.info("Delivery state recorded", extra={"session_date": session_date})

    return {
        "session_date": (analytics.session_date.isoformat()),
        "portfolio_value": (analytics.portfolio_value),
        "portfolio_return": (analytics.portfolio_return),
        "message_id": message_id,
    }
