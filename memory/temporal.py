from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional


# ============================================================
# DATA MODEL
# ============================================================

@dataclass
class TemporalExpression:
    text: str
    expression_type: str
    normalized_value: str
    start: int
    end: int


# ============================================================
# TEMPORAL ENRICHER
# ============================================================

class TemporalEnricher:

    RELATIVE_EXPRESSIONS = {
        "сегодня": 0,
        "вчера": -1,
        "позавчера": -2,
        "завтра": 1,
        "послезавтра": 2,
    }

    CURRENT_EXPRESSIONS = {
        "сейчас",
        "теперь",
    }

    # --------------------------------------------------------
    # через неделю
    # через 3 дня
    # через 2 месяца
    # через 5 лет
    # --------------------------------------------------------

    SIMPLE_RELATIVE_PATTERN = re.compile(
        r"\bчерез\s+"
        r"(?:(\d+)\s+)?"
        r"(день|дня|дней|"
        r"неделю|недели|недель|"
        r"месяц|месяца|месяцев|"
        r"год|года|лет)"
        r"\b",
        re.IGNORECASE,
    )

    # --------------------------------------------------------
    # 2024
    # 2024 год
    # 2024 года
    # 2024 году
    # --------------------------------------------------------

    YEAR_PATTERN = re.compile(
        r"\b"
        r"(19\d{2}|20\d{2}|21\d{2})"
        r"(?:\s+г(?:од|ода|оду))?"
        r"\b",
        re.IGNORECASE,
    )

    # --------------------------------------------------------
    # 15 августа
    # 15 августа 2025
    # 15 августа 2025 года
    # --------------------------------------------------------

    MONTHS = {
        "января": 1,
        "февраля": 2,
        "марта": 3,
        "апреля": 4,
        "мая": 5,
        "июня": 6,
        "июля": 7,
        "августа": 8,
        "сентября": 9,
        "октября": 10,
        "ноября": 11,
        "декабря": 12,
    }

    DATE_PATTERN = re.compile(
        r"\b"
        r"(\d{1,2})\s+"
        r"(января|февраля|марта|апреля|мая|июня|"
        r"июля|августа|сентября|октября|ноября|декабря)"
        r"(?:\s+(\d{4}))?"
        r"(?:\s+г(?:од|ода|оду))?"
        r"\b",
        re.IGNORECASE,
    )

    # ========================================================
    # CONSTRUCTOR
    # ========================================================

    def __init__(
        self,
        reference_date: Optional[date] = None,
    ) -> None:

        self.reference_date = (
            reference_date
            if reference_date is not None
            else date.today()
        )

    # ========================================================
    # ADD MONTHS
    # ========================================================

    def _add_months(
        self,
        source_date: date,
        months: int,
    ) -> date:

        total_months = (
            source_date.year * 12
            + (source_date.month - 1)
            + months
        )

        target_year = (
            total_months // 12
        )

        target_month = (
            total_months % 12 + 1
        )

        last_day = monthrange(
            target_year,
            target_month,
        )[1]

        target_day = min(
            source_date.day,
            last_day,
        )

        return date(
            target_year,
            target_month,
            target_day,
        )

    # ========================================================
    # ADD YEARS
    # ========================================================

    def _add_years(
        self,
        source_date: date,
        years: int,
    ) -> date:

        target_year = (
            source_date.year + years
        )

        try:

            return date(
                target_year,
                source_date.month,
                source_date.day,
            )

        except ValueError:

            return date(
                target_year,
                source_date.month,
                28,
            )

    # ========================================================
    # PUBLIC API
    # ========================================================

    def extract(
        self,
        text: str,
    ) -> list[TemporalExpression]:

        if not text.strip():
            return []

        expressions: list[
            TemporalExpression
        ] = []

        date_expressions = (
            self._extract_dates(text)
        )

        expressions.extend(
            date_expressions
        )

        year_expressions = (
            self._extract_years(
                text,
                existing=expressions,
            )
        )

        expressions.extend(
            year_expressions
        )

        relative_expressions = (
            self._extract_relative(text)
        )

        expressions.extend(
            relative_expressions
        )

        expressions.sort(
            key=lambda expression: (
                expression.start,
                expression.end,
            )
        )

        return expressions

    # ========================================================
    # DATES
    # ========================================================

    def _extract_dates(
        self,
        text: str,
    ) -> list[TemporalExpression]:

        result = []

        for match in self.DATE_PATTERN.finditer(
            text
        ):

            day = int(
                match.group(1)
            )

            month = self.MONTHS[
                match.group(2).lower()
            ]

            year_str = match.group(3)

            if year_str is not None:

                year = int(
                    year_str
                )

                try:

                    normalized_value = date(
                        year,
                        month,
                        day,
                    ).isoformat()

                except ValueError:

                    continue

                expression_type = "absolute"

            else:

                normalized_value = (
                    f"--{month:02d}-{day:02d}"
                )

                expression_type = (
                    "absolute_partial"
                )

            result.append(
                TemporalExpression(
                    text=match.group(0),
                    expression_type=expression_type,
                    normalized_value=normalized_value,
                    start=match.start(),
                    end=match.end(),
                )
            )

        return result

    # ========================================================
    # YEARS
    # ========================================================

    def _extract_years(
        self,
        text: str,
        existing: list[TemporalExpression],
    ) -> list[TemporalExpression]:

        result = []

        for match in self.YEAR_PATTERN.finditer(
            text
        ):

            start = match.start()
            end = match.end()

            inside_existing = False

            for expression in existing:

                if (
                    expression.start <= start
                    and end <= expression.end
                ):

                    inside_existing = True
                    break

            if inside_existing:
                continue

            year = int(
                match.group(1)
            )

            result.append(
                TemporalExpression(
                    text=match.group(0),
                    expression_type="absolute",
                    normalized_value=str(
                        year
                    ),
                    start=start,
                    end=end,
                )
            )

        return result

    # ========================================================
    # RELATIVE
    # ========================================================

    def _extract_relative(
        self,
        text: str,
    ) -> list[TemporalExpression]:

        result = []

        all_simple = (
            set(
                self.RELATIVE_EXPRESSIONS
            )
            |
            self.CURRENT_EXPRESSIONS
        )

        for expression in all_simple:

            pattern = re.compile(
                rf"\b{re.escape(expression)}\b",
                re.IGNORECASE,
            )

            for match in pattern.finditer(
                text
            ):

                expression_lower = (
                    expression.lower()
                )

                if (
                    expression_lower
                    in self.CURRENT_EXPRESSIONS
                ):

                    result.append(
                        TemporalExpression(
                            text=match.group(0),
                            expression_type="current",
                            normalized_value="current",
                            start=match.start(),
                            end=match.end(),
                        )
                    )

                    continue

                offset = (
                    self.RELATIVE_EXPRESSIONS[
                        expression_lower
                    ]
                )

                target_date = (
                    self.reference_date
                    + timedelta(
                        days=offset
                    )
                )

                result.append(
                    TemporalExpression(
                        text=match.group(0),
                        expression_type="relative",
                        normalized_value=(
                            target_date.isoformat()
                        ),
                        start=match.start(),
                        end=match.end(),
                    )
                )

        # ----------------------------------------------------
        # через N ...
        # ----------------------------------------------------

        for match in self.SIMPLE_RELATIVE_PATTERN.finditer(
            text
        ):

            amount_str = match.group(1)

            unit = match.group(2).lower()

            amount = (
                int(amount_str)
                if amount_str is not None
                else 1
            )

            if unit in {
                "день",
                "дня",
                "дней",
            }:

                target_date = (
                    self.reference_date
                    + timedelta(
                        days=amount
                    )
                )

            elif unit in {
                "неделю",
                "недели",
                "недель",
            }:

                target_date = (
                    self.reference_date
                    + timedelta(
                        weeks=amount
                    )
                )

            elif unit in {
                "месяц",
                "месяца",
                "месяцев",
            }:

                target_date = (
                    self._add_months(
                        self.reference_date,
                        amount,
                    )
                )

            elif unit in {
                "год",
                "года",
                "лет",
            }:

                target_date = (
                    self._add_years(
                        self.reference_date,
                        amount,
                    )
                )

            else:
                continue

            result.append(
                TemporalExpression(
                    text=match.group(0),
                    expression_type="relative",
                    normalized_value=(
                        target_date.isoformat()
                    ),
                    start=match.start(),
                    end=match.end(),
                )
            )

        return result