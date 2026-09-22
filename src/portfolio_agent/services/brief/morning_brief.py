import json
import os
from dataclasses import dataclass, field
from datetime import date

from dotenv import load_dotenv
from openai import OpenAI

from portfolio_agent.config import settings
from portfolio_agent.services.research.finnhub_news import NewsArticle

load_dotenv()

MODEL = settings.openai_model

INPUT_COST_PER_MILLION = 0.20
OUTPUT_COST_PER_MILLION = 1.20


class MorningBriefError(Exception):
    """Raised when the morning brief cannot be generated."""

    pass


@dataclass
class BriefChange:
    symbol: str
    return_pct: float
    dollar_impact: float
    explanation: str
    thesis_implication: str
    supporting_articles: list[NewsArticle] = field(default_factory=list)


@dataclass
class BriefAdvice:
    symbol: str
    portfolio_weight: float
    outlook: str
    advice: str
    watch_items: list[str] = field(default_factory=list)
    supporting_articles: list[NewsArticle] = field(default_factory=list)


@dataclass
class MorningBrief:
    date: date
    previous_session_date: date
    session_date: date

    portfolio_value: float
    dollar_change: float
    portfolio_return: float

    benchmark_symbol: str
    benchmark_return: float
    relative_return: float

    cash: float

    changes: list[BriefChange]
    advice: list[BriefAdvice]

    input_tokens: int
    output_tokens: int
    estimated_cost: float
    session_chart_svg: str | None = None


class MorningBriefBuilder:
    """Turns portfolio research into a polished morning brief."""

    def __init__(
        self,
        api_key: str | None = None,
    ):
        api_key = api_key or os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise MorningBriefError("OPENAI_API_KEY was not found in the environment.")

        self.client = OpenAI(api_key=api_key)

    def build(
        self,
        analytics,
        movement_results,
        forward_results,
        session_chart_svg: str | None = None,
        brief_date: date | None = None,
    ) -> MorningBrief:
        """Build the final editorial content for the morning email."""

        brief_date = brief_date or date.today()

        context = self._build_context(
            analytics=analytics,
            movement_results=movement_results,
            forward_results=forward_results,
        )

        try:
            response = self.client.responses.create(
                model=MODEL,
                reasoning={"effort": "low"},
                instructions=(
                    "You are the editor of a personal morning "
                    "portfolio brief. The research and investment "
                    "reasoning have already been completed. Your "
                    "job is to turn the supplied verified information "
                    "into a polished 60-90 second morning read. "
                    "Do not perform new research. Do not invent facts, "
                    "events, catalysts, forecasts, prices, or investment "
                    "conclusions. Use only the supplied information. "
                    "The brief has two main editorial sections: "
                    "Changes and Agentic Advice. "
                    "CHANGES explains meaningful developments from "
                    "the latest market session. For each included "
                    "security, explain what happened and the most "
                    "important evidence for why it happened in roughly "
                    "2-3 concise sentences. Then write one additional "
                    "short sentence explaining what the development "
                    "means for the longer-term thesis. "
                    "Do not repeat the security's daily percentage "
                    "return or dollar portfolio impact in the prose. "
                    "Those values are displayed separately. "
                    "The thesis implication should be useful rather "
                    "than mechanical. For example, explain whether "
                    "the development materially changes, reinforces, "
                    "weakens, or simply leaves the longer-term thesis "
                    "intact. Do not expose internal change-type labels. "
                    "AGENTIC ADVICE tells the investor what to do or "
                    "consider doing with positions that genuinely "
                    "deserve attention. Preserve the substance of the "
                    "supplied portfolio advice while improving clarity "
                    "and removing repetition. Aim for roughly 3-5 "
                    "sentences per included position. "
                    "The advice should explain the recommended posture, "
                    "why that posture makes sense, and the most "
                    "important condition that could change it. Consider "
                    "portfolio concentration and position size when "
                    "they are relevant. "
                    "Do not merely restate the Changes section inside "
                    "Agentic Advice. Changes answers what happened. "
                    "Agentic Advice answers what the investor should "
                    "do about the position going forward. "
                    "Not every researched position must appear in "
                    "Agentic Advice. Include a position when its size, "
                    "recent movement, thesis development, risk, or "
                    "opportunity gives the investor a meaningful reason "
                    "to think about it today. "
                    "The supplied outlook label may be shown because "
                    "it gives useful context. Do not expose internal "
                    "confidence scores, change classifications, article "
                    "IDs, ranking scores, token usage, or model "
                    "reasoning. "
                    "For watch items, turn detailed research topics "
                    "into short, readable labels. Each label should "
                    "normally be two to five words. Examples include "
                    "'FY28 growth', 'AI demand', 'Margins & cash flow', "
                    "'Cloud growth', and 'Search competition'. Return "
                    "no more than three watch items per position. "
                    "The tone should resemble a high-quality personal "
                    "investment analyst briefing: concise, analytical, "
                    "plain-English, and useful. It should contain enough "
                    "context to teach the investor something without "
                    "feeling like a research report."
                ),
                input=(
                    "Create today's morning portfolio brief from "
                    "the following verified portfolio and research "
                    "information:\n\n"
                    f"{json.dumps(context, indent=2)}"
                ),
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "morning_portfolio_brief",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "changes": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "symbol": {"type": "string"},
                                            "explanation": {"type": "string"},
                                            "thesis_implication": {"type": "string"},
                                        },
                                        "required": [
                                            "symbol",
                                            "explanation",
                                            "thesis_implication",
                                        ],
                                        "additionalProperties": False,
                                    },
                                },
                                "advice": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "symbol": {"type": "string"},
                                            "advice": {"type": "string"},
                                            "watch_items": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                                "maxItems": 3,
                                            },
                                        },
                                        "required": [
                                            "symbol",
                                            "advice",
                                            "watch_items",
                                        ],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": [
                                "changes",
                                "advice",
                            ],
                            "additionalProperties": False,
                        },
                    }
                },
            )

        except Exception as error:
            raise MorningBriefError(f"Morning brief generation failed: {error}") from error

        try:
            editorial = json.loads(response.output_text)

        except (
            TypeError,
            json.JSONDecodeError,
        ) as error:
            raise MorningBriefError("OpenAI returned an invalid morning brief response.") from error

        input_tokens = response.usage.input_tokens

        output_tokens = response.usage.output_tokens

        estimated_cost = (
            input_tokens / 1_000_000 * INPUT_COST_PER_MILLION
            + output_tokens / 1_000_000 * OUTPUT_COST_PER_MILLION
        )

        changes = self._build_changes(
            editorial=editorial,
            movement_results=movement_results,
        )

        advice = self._build_advice(
            editorial=editorial,
            forward_results=forward_results,
        )

        return MorningBrief(
            date=brief_date,
            previous_session_date=(analytics.previous_session_date),
            session_date=(analytics.session_date),
            portfolio_value=(analytics.portfolio_value),
            dollar_change=(analytics.dollar_change),
            portfolio_return=(analytics.portfolio_return),
            benchmark_symbol=(analytics.benchmark_symbol),
            benchmark_return=(analytics.benchmark_return),
            relative_return=(analytics.relative_return),
            cash=analytics.cash,
            changes=changes,
            advice=advice,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
            session_chart_svg=session_chart_svg,
        )

    @staticmethod
    def _build_context(
        analytics,
        movement_results,
        forward_results,
    ) -> dict:
        """Prepare verified research for the editorial model."""

        movements = []

        for result in movement_results:
            movements.append(
                {
                    "symbol": (result.symbol.upper()),
                    "daily_return_pct": round(
                        result.return_pct * 100,
                        2,
                    ),
                    "dollar_change": round(
                        result.dollar_change,
                        2,
                    ),
                    "portfolio_contribution_pct": round(
                        result.portfolio_contribution * 100,
                        2,
                    ),
                    "movement_explanation": (result.analysis.explanation),
                    "movement_confidence": (result.analysis.confidence),
                }
            )

        forward = []

        for result in forward_results:
            forward.append(
                {
                    "symbol": (result.symbol.upper()),
                    "portfolio_weight_pct": round(
                        result.portfolio_weight * 100,
                        2,
                    ),
                    "daily_return_pct": round(
                        result.return_pct * 100,
                        2,
                    ),
                    "portfolio_contribution_pct": round(
                        result.portfolio_contribution * 100,
                        2,
                    ),
                    "outlook": (result.analysis.outlook),
                    "confidence": (result.analysis.confidence),
                    "thesis_summary": (result.analysis.summary),
                    "change_type": (result.analysis.change_type),
                    "change_summary": (result.analysis.change_summary),
                    "portfolio_advice": (result.advice.advice),
                    "watch_items": [item.topic for item in result.analysis.watch_items],
                }
            )

        return {
            "portfolio": {
                "previous_session_date": (analytics.previous_session_date.isoformat()),
                "session_date": (analytics.session_date.isoformat()),
                "portfolio_value": round(
                    analytics.portfolio_value,
                    2,
                ),
                "daily_dollar_change": round(
                    analytics.dollar_change,
                    2,
                ),
                "daily_return_pct": round(
                    analytics.portfolio_return * 100,
                    2,
                ),
                "benchmark_symbol": (analytics.benchmark_symbol),
                "benchmark_return_pct": round(
                    analytics.benchmark_return * 100,
                    2,
                ),
                "relative_return_pct": round(
                    analytics.relative_return * 100,
                    2,
                ),
                "cash": round(
                    analytics.cash,
                    2,
                ),
            },
            "movements": movements,
            "forward_research": forward,
        }

    @staticmethod
    def _build_changes(
        editorial: dict,
        movement_results,
    ) -> list[BriefChange]:
        """Combine editorial copy with deterministic movement facts."""

        movement_by_symbol = {result.symbol.upper(): result for result in movement_results}

        changes = []

        for item in editorial["changes"]:
            symbol = item["symbol"].upper()

            result = movement_by_symbol.get(symbol)

            if result is None:
                continue

            changes.append(
                BriefChange(
                    symbol=symbol,
                    return_pct=(result.return_pct),
                    dollar_impact=(result.dollar_change),
                    explanation=(item["explanation"]),
                    thesis_implication=(item["thesis_implication"]),
                    supporting_articles=result.supporting_articles,
                )
            )

        return changes

    @staticmethod
    def _build_advice(
        editorial: dict,
        forward_results,
    ) -> list[BriefAdvice]:
        """Combine editorial advice with deterministic portfolio facts."""

        forward_by_symbol = {result.symbol.upper(): result for result in forward_results}

        advice_results = []

        for item in editorial["advice"]:
            symbol = item["symbol"].upper()

            result = forward_by_symbol.get(symbol)

            if result is None:
                continue

            advice_results.append(
                BriefAdvice(
                    symbol=symbol,
                    portfolio_weight=(result.portfolio_weight),
                    outlook=(result.analysis.outlook),
                    advice=(item["advice"]),
                    watch_items=(item["watch_items"]),
                    supporting_articles=result.supporting_articles,
                )
            )

        return advice_results
