from html import escape
from pathlib import Path

PAGE_BACKGROUND = "#1a1b26"
CONTENT_BACKGROUND = "#24283b"
TEXT_PRIMARY = "#c0caf5"
TEXT_SECONDARY = "#a9b1d6"
TEXT_MUTED = "#7982a9"
SOFT_BACKGROUND = "#1f2335"
SOFT_BACKGROUND_HOVER = "#292e42"
BORDER = "#414868"

POSITIVE = "#9ece6a"
NEGATIVE = "#f7768e"
NEUTRAL = "#7aa2f7"
LINK = "#7dcfff"

FONT_STACK = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"


class EmailRenderer:
    """Render a MorningBrief as a Tokyo Night themed HTML email."""

    def render(self, brief) -> str:
        """Render the complete morning brief."""

        changes_html = self._render_changes(brief.changes)

        advice_html = self._render_advice(brief.advice)

        if not changes_html and not advice_html:
            body_sections = self._render_quiet_morning()
        else:
            body_sections = changes_html + advice_html

        brief_date = self._format_date(brief.date)

        session_description = self._format_session_description(
            brief_date=brief.date,
            session_date=brief.session_date,
        )

        return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="color-scheme" content="dark">
  <meta name="supported-color-schemes" content="dark">
  <meta
    name="viewport"
    content="width=device-width, initial-scale=1"
  >
  <style>
    :root {{
      color-scheme: dark;
      supported-color-schemes: dark;
    }}
  </style>
  <title>Andrew's Morning Portfolio Brief</title>
</head>

<body
  bgcolor="{PAGE_BACKGROUND}"
  style="
    margin:0;
    padding:0;
    background:{PAGE_BACKGROUND};
    background-color:{PAGE_BACKGROUND};
    font-family:{FONT_STACK};
    color:{TEXT_PRIMARY};
    color-scheme:dark;
    -webkit-font-smoothing:antialiased;
  "
>
  <table
    role="presentation"
    width="100%"
    cellspacing="0"
    cellpadding="0"
    border="0"
    bgcolor="{PAGE_BACKGROUND}"
    style="
      width:100%;
      background:{PAGE_BACKGROUND};
      background-color:{PAGE_BACKGROUND};
    "
  >
    <tr>
      <td
        align="center"
        style="
          padding:48px 18px;
        "
      >

        <table
          role="presentation"
          width="100%"
          cellspacing="0"
          cellpadding="0"
          border="0"
          bgcolor="{CONTENT_BACKGROUND}"
          style="
            width:100%;
            max-width:680px;
            background:{CONTENT_BACKGROUND};
            background-color:{CONTENT_BACKGROUND};
            border-radius:12px;
          "
        >

          <tr>
            <td
              style="
                padding:
                  48px
                  52px
                  26px;
              "
            >

              <div
                style="
                  font-size:27px;
                  line-height:1.25;
                  font-weight:650;
                  letter-spacing:-0.6px;
                  color:{TEXT_PRIMARY};
                "
              >
                Andrew's Morning Portfolio Brief
              </div>

              <div
                style="
                  margin-top:9px;
                  font-size:14px;
                  line-height:1.5;
                  color:{TEXT_SECONDARY};
                "
              >
                {escape(brief_date)}
              </div>

              <div
                style="
                  margin-top:2px;
                  font-size:13px;
                  line-height:1.5;
                  color:{TEXT_MUTED};
                "
              >
                {escape(session_description)}
              </div>

            </td>
          </tr>

          <tr>
            <td
              style="
                padding:
                  28px
                  52px
                  44px;
              "
            >

              {self._section_title("Portfolio")}

              <div
                style="
                  margin-top:22px;
                  font-size:36px;
                  line-height:1.08;
                  font-weight:650;
                  letter-spacing:-1.2px;
                  color:{TEXT_PRIMARY};
                "
              >
                ${brief.portfolio_value:,.2f}
              </div>

              <div
                style="
                  margin-top:6px;
                  font-size:13px;
                  color:{TEXT_SECONDARY};
                "
              >
                Portfolio value
              </div>

              <div
                style="
                  margin-top:24px;
                  padding:
                    15px
                    18px;
                  background:{SOFT_BACKGROUND};
                  border-radius:8px;
                "
              >

                <table
                  role="presentation"
                  width="100%"
                  cellspacing="0"
                  cellpadding="0"
                  border="0"
                >

                  {
            self._metric_row(
                "Session",
                self._money_percent(
                    brief.dollar_change,
                    brief.portfolio_return,
                ),
                brief.portfolio_return,
            )
        }

                  {
            self._metric_row(
                brief.benchmark_symbol,
                (f"{brief.benchmark_return:+.2%}"),
                brief.benchmark_return,
            )
        }

                  {
            self._metric_row(
                (f"vs. {brief.benchmark_symbol}"),
                (f"{brief.relative_return:+.2%}"),
                brief.relative_return,
            )
        }

                </table>

              </div>

              {getattr(brief, "session_chart_svg", None) or ""}

            </td>
          </tr>

          {body_sections}

          <tr>
            <td
              style="
                padding:
                  10px
                  52px
                  44px;
              "
            >

              <div
                style="
                  padding-top:22px;
                  border-top:
                    1px solid {BORDER};
                  font-size:11px;
                  line-height:1.6;
                  color:{TEXT_MUTED};
                "
              >
                Personal portfolio research generated
                from completed-session market data.
                Investment information is for informational
                purposes and is not a guarantee of
                future results.
              </div>

            </td>
          </tr>

        </table>

      </td>
    </tr>
  </table>
</body>
</html>"""

    def write(
        self,
        brief,
        output_path: str | Path = ("output/morning_brief.html"),
    ) -> Path:
        """Write the rendered email to disk."""

        path = Path(output_path)

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        path.write_text(
            self.render(brief),
            encoding="utf-8",
        )

        return path

    def _render_changes(
        self,
        changes,
    ) -> str:
        """Render meaningful completed-session changes."""

        if not changes:
            return ""

        cards = []

        for change in changes:
            cards.append(self._render_change_card(change))

        return f"""
          <tr>
            <td
              style="
                padding:
                  6px
                  52px
                  44px;
              "
            >

              {self._section_title("Changes")}

              <div
                style="
                  margin-top:20px;
                "
              >
                {"".join(cards)}
              </div>

            </td>
          </tr>
        """

    def _render_change_card(
        self,
        change,
    ) -> str:
        """Render one completed-session movement."""

        direction = self._direction_color(change.return_pct)

        return f"""
          <div
            style="
              margin-bottom:14px;
              padding:
                22px
                22px
                21px;
              background:{SOFT_BACKGROUND};
              border-radius:8px;
            "
          >

            <table
              role="presentation"
              width="100%"
              cellspacing="0"
              cellpadding="0"
              border="0"
            >
              <tr>

                <td
                  style="
                    font-size:17px;
                    font-weight:650;
                    color:{TEXT_PRIMARY};
                  "
                >
                  {escape(change.symbol)}
                </td>

                <td
                  align="right"
                  style="
                    font-size:16px;
                    font-weight:650;
                    color:{direction};
                  "
                >
                  {change.return_pct:+.2%}
                </td>

              </tr>
            </table>

            <div
              style="
                margin-top:5px;
                font-size:12px;
                color:{TEXT_SECONDARY};
              "
            >
              Portfolio impact
              &nbsp;
              <span
                style="
                  font-weight:550;
                  color:{direction};
                "
              >
                {self._format_money(change.dollar_impact)}
              </span>
            </div>

            <div
              style="
                margin-top:18px;
                font-size:15px;
                line-height:1.65;
                color:{TEXT_PRIMARY};
              "
            >
              {escape(change.explanation)}
            </div>

            <div
              style="
                margin-top:13px;
                font-size:14px;
                line-height:1.65;
                color:{TEXT_SECONDARY};
              "
            >
              {escape(change.thesis_implication)}
            </div>

            {self._render_sources(change.supporting_articles)}

          </div>
        """

    def _render_advice(
        self,
        advice_items,
    ) -> str:
        """Render portfolio-specific agentic advice."""

        if not advice_items:
            return ""

        items = []

        for index, item in enumerate(advice_items):
            items.append(
                self._render_advice_item(
                    item=item,
                    include_divider=(index > 0),
                )
            )

        return f"""
          <tr>
            <td
              style="
                padding:
                  4px
                  52px
                  48px;
              "
            >

              {self._section_title("Agentic Advice")}

              <div
                style="
                  margin-top:18px;
                "
              >
                {"".join(items)}
              </div>

            </td>
          </tr>
        """

    def _render_advice_item(
        self,
        item,
        include_divider: bool,
    ) -> str:
        """Render advice for one holding."""

        divider = ""

        if include_divider:
            divider = f"border-top:1px solid {BORDER};"

        watches = "".join(self._watch_pill(watch) for watch in item.watch_items)

        outlook = self._format_outlook(item.outlook)

        return f"""
          <div
            style="
              padding:
                24px
                0
                28px;
              {divider}
            "
          >

            <div
              style="
                font-size:18px;
                line-height:1.3;
                font-weight:650;
                color:{TEXT_PRIMARY};
              "
            >
              {escape(item.symbol)}
            </div>

            <div
              style="
                margin-top:5px;
                font-size:12px;
                line-height:1.5;
                color:{TEXT_SECONDARY};
              "
            >
              {item.portfolio_weight:.1%}
              of portfolio
              &nbsp;·&nbsp;
              {escape(outlook)}
            </div>

            <div
              style="
                margin-top:16px;
                font-size:15px;
                line-height:1.7;
                color:{TEXT_PRIMARY};
              "
            >
              {escape(item.advice)}
            </div>

            {self._render_sources(item.supporting_articles)}

            {self._watching_html(watches)}

          </div>
        """

    @staticmethod
    def _section_title(
        title: str,
    ) -> str:
        """Render a document-style section heading."""

        return f"""
          <div
            style="
              font-size:20px;
              line-height:1.3;
              font-weight:650;
              letter-spacing:-0.25px;
              color:{TEXT_PRIMARY};
            "
          >
            {escape(title)}
          </div>
        """

    @staticmethod
    def _render_sources(articles) -> str:
        """Render selected research articles as compact, safe links."""

        links = []
        for article in articles:
            url = (article.url or "").strip()
            if not url.lower().startswith(("https://", "http://")):
                continue

            headline = escape(article.headline.strip() or "Read article")
            source = escape(article.source.strip())
            label = headline
            if source:
                label = (
                    f'{headline} <span style="color:{TEXT_MUTED};">'
                    f"· {source}</span>"
                )
            links.append(
                f'<a href="{escape(url, quote=True)}" '
                f'style="color:{LINK};text-decoration:none;">{label}</a>'
            )

        if not links:
            return ""

        return f"""
          <div style="margin-top:16px;font-size:11px;font-weight:600;color:{TEXT_SECONDARY};">
            Sources
          </div>
          <ul style="
            margin:5px 0 0;
            padding-left:17px;
            font-size:12px;
            line-height:1.6;
            color:{TEXT_SECONDARY};
          ">
            {''.join(f'<li style="margin:3px 0;">{link}</li>' for link in links)}
          </ul>
        """

    @staticmethod
    def _watch_pill(
        watch: str,
    ) -> str:
        """Render one subtle watch topic."""

        return (
            f'<span style="'
            f"display:inline-block;"
            f"margin:6px 6px 0 0;"
            f"padding:5px 9px;"
            f"background:{SOFT_BACKGROUND_HOVER};"
            f"border-radius:5px;"
            f"font-size:11px;"
            f"line-height:1.4;"
            f"color:{TEXT_SECONDARY};"
            f'">'
            f"{escape(watch)}"
            f"</span>"
        )

    @staticmethod
    def _watching_html(
        watches: str,
    ) -> str:
        """Render watch topics beneath advice."""

        if not watches:
            return ""

        return f"""
          <div
            style="
              margin-top:19px;
              font-size:11px;
              font-weight:600;
              color:{TEXT_SECONDARY};
            "
          >
            Watching
          </div>

          <div
            style="
              margin-top:2px;
            "
          >
            {watches}
          </div>
        """

    @staticmethod
    def _render_quiet_morning() -> str:
        """Render the brief when nothing requires attention."""

        return f"""
          <tr>
            <td
              style="
                padding:
                  6px
                  52px
                  48px;
              "
            >

              {EmailRenderer._section_title("Quiet Morning")}

              <div
                style="
                  margin-top:18px;
                  padding:20px 22px;
                  background:{SOFT_BACKGROUND};
                  border-radius:8px;
                  font-size:15px;
                  line-height:1.65;
                  color:{TEXT_PRIMARY};
                "
              >
                No significant portfolio changes
                or developments require attention.
              </div>

            </td>
          </tr>
        """

    @staticmethod
    def _metric_row(
        label: str,
        value: str,
        direction: float,
    ) -> str:
        """Render one portfolio summary metric."""

        color = EmailRenderer._direction_color(direction)

        return f"""
          <tr>

            <td
              style="
                padding:7px 0;
                font-size:13px;
                line-height:1.4;
                color:{TEXT_SECONDARY};
              "
            >
              {escape(label)}
            </td>

            <td
              align="right"
              style="
                padding:7px 0;
                font-size:13px;
                line-height:1.4;
                font-weight:600;
                color:{color};
              "
            >
              {escape(value)}
            </td>

          </tr>
        """

    @staticmethod
    def _format_date(
        value,
    ) -> str:
        """Format a date without a leading zero."""

        return f"{value.strftime('%A, %B')} {value.day}"

    @staticmethod
    def _format_session_description(
        brief_date,
        session_date,
    ) -> str:
        """Describe the completed session naturally."""

        day_difference = (brief_date - session_date).days

        if day_difference == 1:
            return f"{session_date.strftime('%A')}'s completed session"

        return f"Completed session · {session_date.strftime('%A, %B')} {session_date.day}"

    @staticmethod
    def _format_outlook(
        outlook: str,
    ) -> str:
        """Format an outlook for display."""

        return outlook.strip().title()

    @staticmethod
    def _money_percent(
        dollars: float,
        return_pct: float,
    ) -> str:
        """Format dollar and percentage movement."""

        return f"{EmailRenderer._format_money(dollars)} ({return_pct:+.2%})"

    @staticmethod
    def _format_money(
        value: float,
    ) -> str:
        """Format a signed dollar value."""

        if value > 0:
            sign = "+"
        elif value < 0:
            sign = "-"
        else:
            sign = ""

        return f"{sign}${abs(value):,.2f}"

    @staticmethod
    def _direction_color(
        value: float,
    ) -> str:
        """Return a restrained color for directional values."""

        if value > 0:
            return POSITIVE

        if value < 0:
            return NEGATIVE

        return NEUTRAL
