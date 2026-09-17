from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from qwen_model import QwenModel


# ============================================================
# DATA MODELS
# ============================================================

@dataclass
class Mention:
    text: str
    start: int
    end: int


@dataclass
class CoreferenceCluster:
    representative: Mention
    mentions: list[Mention]


# ============================================================
# COREFERENCE RESOLVER
# ============================================================

class CoreferenceResolver:

    SYSTEM_PROMPT = """
Ты решаешь задачу кореференции на русском языке.

Найди группы упоминаний, которые относятся к одному
и тому же объекту или человеку.

Пример:

Текст:
"Иван пришёл домой. Он приготовил ужин."

Ответ:

[
  {
    "representative": "Иван",
    "mentions": ["Иван", "Он"]
  }
]

ВАЖНО:

1. Возвращай ТОЛЬКО JSON-массив.
2. Не используй Markdown.
3. Не добавляй объяснений.
4. Не используй <think>.
5. НЕ добавляй start/end.
6. Указывай только реальные фрагменты исходного текста.
7. representative должен быть одной из строк в mentions.
8. Если кореференции нет, верни [].

Формат:

[
  {
    "representative": "Иван",
    "mentions": ["Иван", "Он"]
  }
]
""".strip()

    def __init__(
        self,
        qwen: QwenModel,
    ) -> None:

        self.qwen = qwen

    # ========================================================
    # FIND SPAN
    # ========================================================

    @staticmethod
    def _find_span(
        text: str,
        mention_text: str,
        used_spans: list[tuple[int, int]],
        start_search: int = 0,
    ) -> tuple[int, int] | None:

        mention_text = mention_text.strip()

        if not mention_text:
            return None

        start = text.find(
            mention_text,
            start_search,
        )

        if start != -1:

            end = (
                start
                + len(mention_text)
            )

            overlap = any(
                start < used_end
                and end > used_start
                for used_start, used_end
                in used_spans
            )

            if not overlap:
                return start, end

        # Fallback

        start = 0

        while True:

            start = text.find(
                mention_text,
                start,
            )

            if start == -1:
                break

            end = (
                start
                + len(mention_text)
            )

            overlap = any(
                start < used_end
                and end > used_start
                for used_start, used_end
                in used_spans
            )

            if not overlap:
                return start, end

            start += 1

        return None

    # ========================================================
    # BUILD CLUSTER
    # ========================================================

    def _build_cluster(
        self,
        text: str,
        raw_cluster: dict[str, Any],
    ) -> CoreferenceCluster:

        representative_text = (
            raw_cluster.get(
                "representative"
            )
        )

        mentions_data = (
            raw_cluster.get(
                "mentions"
            )
        )

        if not isinstance(
            representative_text,
            str,
        ):
            raise ValueError(
                "Representative must be a string."
            )

        if not isinstance(
            mentions_data,
            list,
        ):
            raise ValueError(
                "Mentions must be a list."
            )

        representative_text = (
            representative_text.strip()
        )

        mention_texts = []

        for mention in mentions_data:

            if not isinstance(
                mention,
                str,
            ):
                continue

            mention = mention.strip()

            if mention:
                mention_texts.append(
                    mention
                )

        if (
            representative_text
            not in mention_texts
        ):

            mention_texts.insert(
                0,
                representative_text,
            )

        if len(mention_texts) < 2:
            raise ValueError(
                "A coreference cluster must "
                "contain at least two mentions."
            )

        used_spans = []
        mentions = []

        search_position = 0

        for mention_text in mention_texts:

            if mention_text not in text:
                raise ValueError(
                    f"Mention '{mention_text}' "
                    f"not found in source text."
                )

            span = self._find_span(
                text,
                mention_text,
                used_spans,
                search_position,
            )

            if span is None:
                raise ValueError(
                    f"Could not locate "
                    f"'{mention_text}'."
                )

            start, end = span

            mentions.append(
                Mention(
                    text=mention_text,
                    start=start,
                    end=end,
                )
            )

            used_spans.append(
                (start, end)
            )

            search_position = end

        representative = next(
            (
                mention
                for mention in mentions
                if mention.text
                == representative_text
            ),
            None,
        )

        if representative is None:
            raise ValueError(
                "Representative not found."
            )

        return CoreferenceCluster(
            representative=representative,
            mentions=mentions,
        )

    # ========================================================
    # RESOLVE
    # ========================================================

    def resolve(
        self,
        text: str,
        debug: bool = False,
    ) -> list[CoreferenceCluster]:

        response = self.qwen.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=text,
            max_new_tokens=512,
        )

        if debug:

            print(
                "\n"
                + "=" * 80
            )

            print(
                "RAW COREFERENCE RESPONSE"
            )

            print(
                "=" * 80
            )

            print(
                response
            )

        try:

            data = json.loads(
                response
            )

        except json.JSONDecodeError as e:

            raise ValueError(
                f"Invalid coreference JSON:\n"
                f"{response}"
            ) from e

        if not isinstance(
            data,
            list,
        ):

            raise ValueError(
                "Coreference response "
                "must be a JSON array."
            )

        clusters = []

        for raw_cluster in data:

            try:

                cluster = (
                    self._build_cluster(
                        text,
                        raw_cluster,
                    )
                )

                clusters.append(
                    cluster
                )

            except ValueError as e:

                if debug:

                    print(
                        "Skipped cluster:",
                        e,
                    )

        return clusters

    # ========================================================
    # FORMAT
    # ========================================================

    @staticmethod
    def format_clusters(
        clusters: list[CoreferenceCluster],
    ) -> str:

        if not clusters:
            return "No coreference clusters."

        lines = []

        for i, cluster in enumerate(
            clusters,
            start=1,
        ):

            representative = (
                cluster.representative
            )

            lines.append(
                f"Cluster {i}: "
                f"{representative.text!r} "
                f"[{representative.start}:"
                f"{representative.end}]"
            )

            for mention in cluster.mentions:

                lines.append(
                    f"  - {mention.text!r} "
                    f"[{mention.start}:"
                    f"{mention.end}]"
                )

        return "\n".join(lines)
