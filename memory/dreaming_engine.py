from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from memory_store import MemoryStore
from models import Memory, MemoryStatus, MemoryType


@dataclass
class DreamMergeOperation:
    keep_id: str
    archive_ids: list[str]
    content: str
    reason: str = ""


@dataclass
class DreamArchiveOperation:
    memory_ids: list[str]
    reason: str = ""


@dataclass
class DreamingResult:
    user_id: str
    triggered: bool
    applied: bool
    changed_memories: list[Memory] = field(default_factory=list)
    merge_operations: list[DreamMergeOperation] = field(default_factory=list)
    archive_operations: list[DreamArchiveOperation] = field(default_factory=list)
    summary: str = ""


class DreamingEngine:
    """
    Periodically reorganizes the user's already stored memories.

    Dreaming is deliberately separate from the online write pipeline.
    The online pipeline handles the current message; Dreaming periodically
    reviews accumulated active memories and compresses their history.

    The LLM can only:
      - merge existing memories into one existing memory;
      - archive existing memories.

    It cannot create a new memory id and it never physically deletes data.
    """

    SYSTEM_PROMPT = """
Ты — Dreaming Engine персонального AI-агента.

Ты работаешь ТОЛЬКО с уже сохранённой долговременной памятью пользователя.
Новых фактов у тебя нет и придумывать их запрещено.

Твоя задача — сделать активную память компактнее, сохранив все актуальные
сведения.

ОЧЕНЬ ВАЖНО:
1. Несвязанная с другими memories информация НЕ является устаревшей.
2. Нельзя архивировать memory только потому, что она не относится к текущей
   теме других memories.
3. Если memory не имеет явного признака устаревания, изменения, отмены,
   завершения или дублирования — оставь её ACTIVE.
4. Более новая информация может сделать старое СОСТОЯНИЕ неактуальным, но
   сам по себе тот факт, что memory старая, ещё не является причиной для
   архивации.
5. Поле manual=true означает, что memory была добавлена пользователем
   вручную. Такая memory является полноценной долговременной памятью и
   должна рассматриваться наравне с остальными memories.
6. manual=true НЕ означает, что memory запрещено архивировать. Её можно
   архивировать только при наличии явной причины: дубликат, замена,
   устаревание, отмена, завершение и т.д.

РАЗРЕШЕНО:
1. Объединять действительно связанные memories.
2. Архивировать явно устаревшие, заменённые, отменённые, завершённые,
   поглощённые или дублирующие memories.
3. Учитывать временную последовательность.
4. Сохранять информацию, которая всё ещё актуальна.
5. Архивировать ручную memory, если она действительно дублирует или
   заменена другой существующей memory.

ЗАПРЕЩЕНО:
1. Придумывать сведения.
2. Создавать новый memory id.
3. Использовать id, которого нет во входных memories.
4. Физически удалять memories.
5. Объединять несвязанные факты.
6. Архивировать memory только из-за нерелевантности другой теме.
7. Архивировать memory только потому, что она manual=true.
8. Архивировать memory без явного объяснения причины.

ДЛЯ TRAVEL_PLAN:
Если несколько ACTIVE memories имеют semantic_scope="travel_plan", рассматривай
их как разные свойства одного плана.

Ключевые свойства:
- travel_destination
- travel_month
- travel_status

При конфликте свойств выбирай значение из НОВЕЙШЕЙ memory, которая содержит
это свойство.

Не сохраняй одновременно старое и новое значение одного свойства.

Например, если destination=япония есть в старой memory, а month=ноябрь и
status=запланирован есть в более новой memory, итог должен быть:

Япония + ноябрь + запланирован.

Для merge:
- keep_id обязан быть id одной из входных ACTIVE memories.
- archive_ids должны быть id других входных ACTIVE memories.
- content должен описывать только актуальное объединённое состояние.
- Для одного свойства нельзя одновременно сохранять два конфликтующих значения.

Для archive:
- memory_ids должны быть id входных ACTIVE memories.
- Нужна явная причина устаревания, замены, отмены, завершения или
  дублирования.
- Если memory является дубликатом другой memory, обязательно укажи id этой
  другой memory в reason.
- "Не относится к текущей теме" НЕ является допустимой причиной для archive.
- "Memory старая" без дополнительного объяснения НЕ является допустимой
  причиной для archive.

ДЛЯ MANUAL MEMORY:
Если memory содержит "manual": true, это означает, что пользователь
явно добавил эту запись вручную.
1. Такая memory считается пользовательской и не должна архивироваться
   из-за устаревания, нерелевантности или отсутствия связи с другими
   memories.
2. Manual memory МОЖНО объединить с другой memory, если они содержат
   одну и ту же информацию.
3. В частности, различия регистра, пунктуации и формы написания слова
   сами по себе не делают две одинаковые по смыслу manual memories
   разными фактами.
4. Если две manual memories являются дублями, оставь одну из них ACTIVE,
   а вторую архивируй.
5. При merge итоговая memory должна сохранять manual=true.
Если переработка не нужна — верни пустой массив operations.

ФОРМАТ ОТВЕТА — ТОЛЬКО JSON:

{
  "operations": [
    {
      "type": "merge",
      "keep_id": "ID",
      "archive_ids": ["ID"],
      "content": "Новое актуальное содержание",
      "reason": "Почему записи можно объединить"
    },
    {
      "type": "archive",
      "memory_ids": ["ID"],
      "reason": "Почему запись устарела или дублируется. Для дубликата укажи ID другой memory."
    }
  ],
  "summary": "Краткое описание результата"
}

Не добавляй Markdown, комментарии или текст вне JSON.
""".strip()

    def __init__(
        self,
        store: MemoryStore,
        qwen: Any | None = None,
        every_n_memories: int = 5,
        max_memories_per_run: int = 50,
    ) -> None:
        if every_n_memories < 0:
            raise ValueError("every_n_memories must be >= 0")

        if max_memories_per_run <= 0:
            raise ValueError("max_memories_per_run must be > 0")

        self.store = store
        self.qwen = qwen
        self.every_n_memories = every_n_memories
        self.max_memories_per_run = max_memories_per_run
        self._pending_counts: dict[str, int] = {}

    # Explicit words that indicate that an ACTIVE memory may be obsolete.
    # "Irrelevant to another topic" is intentionally not included.
    _ARCHIVE_REASON_MARKERS = (
        "устар",
        "замен",
        "заменил",
        "заменен",
        "заменён",
        "дублик",
        "дублир",
        "поглощ",
        "отмен",
        "заверш",
        "истек",
        "истёк",
        "больше не",
        "не действует",
        "перестал",
        "перестала",
        "новая версия",
        "новое состояние",
    )

    # =========================================================
    # SCHEDULER
    # =========================================================

    def pending_count(self, user_id: str) -> int:
        return self._pending_counts.get(user_id, 0)

    def reset_counter(self, user_id: str) -> None:
        self._pending_counts.pop(user_id, None)

    def notify_memory_changes(
        self,
        user_id: str,
        count: int,
    ) -> DreamingResult | None:
        """
        Add completed online-memory changes to the per-user counter.

        A threshold of 0 disables automatic Dreaming.
        """
        if count <= 0 or self.every_n_memories == 0:
            return None

        current = self.pending_count(user_id) + count

        if current < self.every_n_memories:
            self._pending_counts[user_id] = current
            return None

        self._pending_counts[user_id] = (
            current % self.every_n_memories
        )

        return self.run(user_id=user_id)

    # =========================================================
    # PROMPT
    # =========================================================

    def _build_prompt(self, memories: list[Memory]) -> str:
        rows: list[dict[str, Any]] = []

        for memory in memories:
            metadata = memory.metadata or {}

            rows.append(
                {
                    "id": memory.id,
                    "type": memory.type.value,
                    "content": memory.content,
                    "status": memory.status.value,
                    "confidence": memory.confidence,
                    "importance": memory.importance,
                    "freshness": memory.freshness,
                    "created_at": (
                        memory.created_at.isoformat()
                        if memory.created_at
                        else None
                    ),
                    "updated_at": (
                        memory.updated_at.isoformat()
                        if memory.updated_at
                        else None
                    ),
                    "valid_from": (
                        memory.valid_from.isoformat()
                        if memory.valid_from
                        else None
                    ),
                    "valid_until": (
                        memory.valid_until.isoformat()
                        if memory.valid_until
                        else None
                    ),
                    "manual": bool(metadata.get("manual", False)),
                    "semantic_scope": metadata.get("semantic_scope"),
                    "semantic_relations": metadata.get(
                        "semantic_relations",
                        [],
                    ),
                    "entities": metadata.get("entities", []),
                }
            )

        return json.dumps(
            {
                "active_memories": rows,
            },
            ensure_ascii=False,
            indent=2,
        )

    # =========================================================
    # JSON PARSING
    # =========================================================

    @staticmethod
    def _parse_response(response: str) -> dict[str, Any]:
        text = (response or "").strip()

        if text.startswith("```"):
            text = re.sub(
                r"^```(?:json)?\s*",
                "",
                text,
            )
            text = re.sub(
                r"\s*```$",
                "",
                text,
            ).strip()

        try:
            data = json.loads(text)

        except json.JSONDecodeError as exc:
            match = re.search(
                r"\{.*\}",
                text,
                re.DOTALL,
            )

            if match is None:
                raise ValueError(
                    "DreamingEngine: Qwen response does not contain JSON."
                ) from exc

            data = json.loads(match.group(0))

        if not isinstance(data, dict):
            raise ValueError(
                "DreamingEngine: response must be a JSON object."
            )

        return data

    # =========================================================
    # SEMANTIC SAFETY HELPERS
    # =========================================================

    @classmethod
    def _has_explicit_archive_reason(
        cls,
        reason: str,
    ) -> bool:
        text = (reason or "").lower()

        return any(
            marker in text
            for marker in cls._ARCHIVE_REASON_MARKERS
        )

    @staticmethod
    def _semantic_relation_keys(
        memory: Memory,
    ) -> set[tuple[str, str, str | None]]:
        metadata = memory.metadata or {}
        raw_relations = metadata.get(
            "semantic_relations",
            [],
        )

        keys: set[
            tuple[str, str, str | None]
        ] = set()

        if not isinstance(raw_relations, list):
            return keys

        for relation in raw_relations:
            if not isinstance(relation, dict):
                continue

            subject = relation.get("subject")
            predicate = relation.get("predicate")
            scope = relation.get("scope")

            if (
                isinstance(subject, str)
                and isinstance(predicate, str)
            ):
                keys.add(
                    (
                        subject,
                        predicate,
                        (
                            scope
                            if isinstance(scope, str)
                            else None
                        ),
                    )
                )

        return keys

    @classmethod
    def _archive_is_semantically_supported(
        cls,
        memory: Memory,
        reason: str,
        active_memories: list[Memory],
    ) -> bool:
        """
        Decide whether an LLM archive operation has enough evidence.

        There are several accepted cases:

        1. The memory and another active memory share semantic_scope.
        2. The memories share semantic relations.
        3. Qwen explicitly says this is a duplicate and references
           another active memory by its real ID.
        4. A memory without semantic metadata contains explicit
           lifecycle language such as "отменился", "заменён", etc.

        Merely saying that a memory is irrelevant is never enough.
        """

        if not cls._has_explicit_archive_reason(reason):
            return False

        scope = (memory.metadata or {}).get(
            "semantic_scope"
        )

        relation_keys = cls._semantic_relation_keys(
            memory
        )

        # -----------------------------------------------------
        # Semantic scope / relations
        # -----------------------------------------------------

        for other in active_memories:
            if other.id == memory.id:
                continue

            other_scope = (other.metadata or {}).get(
                "semantic_scope"
            )

            if (
                isinstance(scope, str)
                and isinstance(other_scope, str)
                and scope == other_scope
            ):
                return True

            other_keys = cls._semantic_relation_keys(
                other
            )

            if (
                relation_keys
                and relation_keys & other_keys
            ):
                return True

        # -----------------------------------------------------
        # Explicit duplicate with another memory ID
        # -----------------------------------------------------

        reason_lower = (reason or "").lower()

        duplicate_markers = (
            "дублик",
            "дублир",
        )

        if any(
            marker in reason_lower
            for marker in duplicate_markers
        ):
            for other in active_memories:
                if other.id == memory.id:
                    continue

                if other.id in reason:
                    return True

        # -----------------------------------------------------
        # Memories without semantic metadata
        # -----------------------------------------------------

        if not scope:
            memory_text = (
                memory.content or ""
            ).lower()

            temporal_markers = (
                "устар",
                "раньше",
                "больше не",
                "перестал",
                "перестала",
                "замен",
                "отмен",
                "заверш",
            )

            return any(
                marker in memory_text
                for marker in temporal_markers
            )

        return False

    @staticmethod
    def _travel_relation_rows(
        memory: Memory,
    ) -> list[dict[str, Any]]:
        metadata = memory.metadata or {}

        raw = metadata.get(
            "semantic_relations",
            [],
        )

        rows: list[dict[str, Any]] = []

        if not isinstance(raw, list):
            return rows

        for relation in raw:
            if not isinstance(relation, dict):
                continue

            subject = relation.get("subject")
            predicate = relation.get("predicate")
            object_value = relation.get("object")
            scope = relation.get("scope")

            if not all(
                isinstance(value, str)
                for value in (
                    subject,
                    predicate,
                    object_value,
                )
            ):
                continue

            if scope != "travel_plan":
                continue

            if predicate not in {
                "travel_destination",
                "travel_month",
                "travel_status",
            }:
                continue

            rows.append(
                {
                    "subject": subject,
                    "predicate": predicate,
                    "object": object_value,
                    "scope": scope,
                }
            )

        return rows

    @classmethod
    def _build_travel_merged_content(
        cls,
        memories: list[Memory],
    ) -> tuple[
        str | None,
        list[dict[str, Any]],
    ]:
        """
        Build deterministic travel-plan content.

        Newest memory wins for each semantic property, but a property
        absent from a newer memory is inherited from an older memory.

        valid_from / created_at are used instead of updated_at because
        the online pipeline can update an old memory in place.
        """

        ordered = sorted(
            memories,
            key=lambda memory: (
                memory.valid_from
                or memory.created_at
                or datetime.min
            ),
            reverse=True,
        )

        selected: dict[
            tuple[str, str, str],
            dict[str, Any],
        ] = {}

        for memory in ordered:
            for relation in cls._travel_relation_rows(
                memory
            ):
                key = (
                    relation["subject"],
                    relation["predicate"],
                    relation["scope"],
                )

                if key not in selected:
                    selected[key] = relation

        rows = list(selected.values())

        if not rows:
            return None, []

        destination = None
        month = None
        status = None

        for relation in rows:
            predicate = relation["predicate"]

            if predicate == "travel_destination":
                destination = relation["object"]

            elif predicate == "travel_month":
                month = relation["object"]

            elif predicate == "travel_status":
                status = relation["object"]

        destination_display = {
            "италия": "Италия",
            "япония": "Япония",
            "испания": "Испания",
            "франция": "Франция",
            "германия": "Германия",
            "турция": "Турция",
            "тайланд": "Таиланд",
            "китай": "Китай",
            "корея": "Корея",
        }.get(
            destination,
            destination,
        )

        month_display = {
            "январь": "январь",
            "февраль": "февраль",
            "март": "март",
            "апрель": "апрель",
            "май": "май",
            "июнь": "июнь",
            "июль": "июль",
            "август": "август",
            "сентябрь": "сентябрь",
            "октябрь": "октябрь",
            "ноябрь": "ноябрь",
            "декабрь": "декабрь",
        }.get(
            month,
            month,
        )

        parts: list[str] = []

        if destination_display:
            parts.append(
                str(destination_display)
            )

        if month_display:
            parts.append(
                str(month_display)
            )

        content = "Актуальный план поездки"

        if parts:
            content += ": " + ", ".join(parts)

        if status:
            status_display = {
                "запланирован": "запланирован",
                "отменён": "отменён",
            }.get(
                status,
                status,
            )

            content += (
                "; статус: "
                + str(status_display)
            )

        return (
            content + ".",
            rows,
        )

    # =========================================================
    # VALIDATION
    # =========================================================

    def _validate_operations(
        self,
        data: dict[str, Any],
        memories: list[Memory],
    ) -> tuple[
        list[DreamMergeOperation],
        list[DreamArchiveOperation],
        str,
    ]:
        by_id = {
            memory.id: memory
            for memory in memories
        }

        # `or []` (not just a .get default) because the model frequently
        # answers "nothing to change" with an explicit `"operations": null`
        # rather than `[]` — a valid "no-op" response, not a malformed one,
        # so it must not be treated as an error.
        operations = data.get(
            "operations",
        ) or []

        if not isinstance(
            operations,
            list,
        ):
            raise ValueError(
                "DreamingEngine: operations must be a list."
            )

        merges: list[
            DreamMergeOperation
        ] = []

        archives: list[
            DreamArchiveOperation
        ] = []

        touched: set[str] = set()

        for raw_operation in operations:
            if not isinstance(
                raw_operation,
                dict,
            ):
                continue

            operation_type = raw_operation.get(
                "type"
            )

            reason = str(
                raw_operation.get(
                    "reason",
                    "",
                )
            ).strip()

            # =================================================
            # MERGE
            # =================================================

            if operation_type == "merge":
                keep_id = raw_operation.get(
                    "keep_id"
                )

                archive_ids = raw_operation.get(
                    "archive_ids",
                    [],
                )

                content = raw_operation.get(
                    "content"
                )

                if not isinstance(
                    keep_id,
                    str,
                ):
                    continue

                if keep_id not in by_id:
                    continue

                if not isinstance(
                    archive_ids,
                    list,
                ):
                    continue

                if (
                    not isinstance(
                        content,
                        str,
                    )
                    or not content.strip()
                ):
                    continue

                archive_ids = [
                    item
                    for item in archive_ids
                    if isinstance(
                        item,
                        str,
                    )
                ]

                if not archive_ids:
                    continue

                if keep_id in archive_ids:
                    continue

                if any(
                    item not in by_id
                    for item in archive_ids
                ):
                    continue

                operation_ids = {
                    keep_id,
                    *archive_ids,
                }

                if touched & operation_ids:
                    continue

                if (
                    by_id[keep_id].status
                    != MemoryStatus.ACTIVE
                ):
                    continue

                if any(
                    by_id[item].status
                    != MemoryStatus.ACTIVE
                    for item in archive_ids
                ):
                    continue

                merges.append(
                    DreamMergeOperation(
                        keep_id=keep_id,
                        archive_ids=archive_ids,
                        content=content.strip(),
                        reason=reason,
                    )
                )

                touched.update(
                    operation_ids
                )

                continue

            # =================================================
            # ARCHIVE
            # =================================================

            if operation_type == "archive":
                memory_ids = raw_operation.get(
                    "memory_ids",
                    [],
                )

                if not isinstance(
                    memory_ids,
                    list,
                ):
                    continue

                valid_memory_ids: list[str] = []

                for memory_id in memory_ids:
                    if not isinstance(
                        memory_id,
                        str,
                    ):
                        continue

                    if memory_id not in by_id:
                        continue

                    memory = by_id[memory_id]

                    if (
                        memory.status
                        != MemoryStatus.ACTIVE
                    ):
                        continue

                    if not self._archive_is_semantically_supported(
                        memory,
                        reason,
                        memories,
                    ):
                        continue

                    valid_memory_ids.append(
                        memory_id
                    )

                if not valid_memory_ids:
                    continue

                operation_ids = set(
                    valid_memory_ids
                )

                if touched & operation_ids:
                    continue

                archives.append(
                    DreamArchiveOperation(
                        memory_ids=valid_memory_ids,
                        reason=reason,
                    )
                )

                touched.update(
                    operation_ids
                )

        # Same null-vs-missing distinction as `operations` above: a `null`
        # summary is a valid "nothing to say" response, not str(None)=="None".
        summary = data.get("summary") or ""
        summary = summary.strip() if isinstance(summary, str) else ""

        return (
            merges,
            archives,
            summary,
        )

    # =========================================================
    # APPLY
    # =========================================================

    @staticmethod
    def _dream_metadata(
        memory: Memory,
    ) -> dict[str, Any]:
        metadata = memory.metadata or {}

        dreaming = metadata.get(
            "dreaming",
            {},
        )

        if not isinstance(
            dreaming,
            dict,
        ):
            dreaming = {}

        return dreaming

    def _archive(
        self,
        memory: Memory,
        reason: str,
    ) -> None:
        now = datetime.utcnow()

        memory.status = MemoryStatus.ARCHIVED

        # Keep the original validity end date when it already exists.
        # Otherwise the archive itself becomes the first terminal timestamp.
        if memory.valid_until is None:
            memory.valid_until = now

        memory.updated_at = now

        if memory.metadata is None:
            memory.metadata = {}

        dreaming = self._dream_metadata(
            memory
        )

        dreaming["archived_at"] = (
            now.isoformat()
        )

        dreaming["archive_reason"] = reason

        memory.metadata["dreaming"] = (
            dreaming
        )

        self.store.update(
            memory
        )

    def _merge(
        self,
        operation: DreamMergeOperation,
        by_id: dict[str, Memory],
    ) -> list[Memory]:
        keep = by_id[
            operation.keep_id
        ]

        changed: list[Memory] = []

        merge_memories = [
            by_id[
                operation.keep_id
            ],
            *[
                by_id[memory_id]
                for memory_id
                in operation.archive_ids
            ],
        ]

        # For structured travel plans, do not trust free-form LLM content
        # when it can contradict the structured semantic relations.
        (
            travel_content,
            travel_relations,
        ) = self._build_travel_merged_content(
            merge_memories
        )

        # Archive old memories first.
        for archive_id in operation.archive_ids:
            memory = by_id[
                archive_id
            ]

            self._archive(
                memory,
                operation.reason,
            )

            changed.append(
                memory
            )

        # Update surviving memory.
        keep.content = (
            travel_content
            if travel_content is not None
            else operation.content
        )

        keep.freshness = 1.0
        keep.updated_at = datetime.utcnow()

        if keep.metadata is None:
            keep.metadata = {}

        if travel_relations:
            keep.metadata[
                "semantic_scope"
            ] = "travel_plan"

            keep.metadata[
                "semantic_relations"
            ] = travel_relations

            existing_relations = []

            for relation in travel_relations:
                existing_relations.append(
                    {
                        "subject": relation[
                            "subject"
                        ],
                        "predicate": relation[
                            "predicate"
                        ],
                        "object": relation[
                            "object"
                        ],
                        "confidence": keep.confidence,
                        "temporal_value": None,
                        "temporal_text": None,
                    }
                )

            keep.metadata[
                "relations"
            ] = existing_relations

        dreaming = self._dream_metadata(
            keep
        )

        dreaming[
            "last_dreamed_at"
        ] = keep.updated_at.isoformat()

        dreaming[
            "merge_reason"
        ] = operation.reason

        dreaming[
            "archived_memory_ids"
        ] = list(
            operation.archive_ids
        )

        keep.metadata[
            "dreaming"
        ] = dreaming

        self.store.update(
            keep
        )

        changed.append(
            keep
        )

        return changed

    # =========================================================
    # RUN
    # =========================================================

    def run(
        self,
        user_id: str,
        apply: bool = True,
    ) -> DreamingResult:
        """Run Dreaming immediately for one user."""

        if self.qwen is None:
            raise RuntimeError(
                "DreamingEngine requires the shared Qwen model."
            )

        now = datetime.utcnow()

        all_active = self.store.find_by_user(
            user_id=user_id,
            active_only=True,
        )

        # -----------------------------------------------------
        # Deterministic expiration
        # -----------------------------------------------------

        expired = [
            memory
            for memory in all_active
            if (
                memory.valid_until is not None
                and memory.valid_until <= now
            )
        ]

        result = DreamingResult(
            user_id=user_id,
            triggered=True,
            applied=False,
        )

        if apply:
            for memory in expired:
                self._archive(
                    memory,
                    "Memory validity period has expired.",
                )

                result.changed_memories.append(
                    memory
                )

                result.archive_operations.append(
                    DreamArchiveOperation(
                        memory_ids=[
                            memory.id
                        ],
                        reason=(
                            "Memory validity period has expired."
                        ),
                    )
                )

        # -----------------------------------------------------
        # Remaining active memories
        # -----------------------------------------------------

        remaining_active = [
            memory
            for memory in all_active
            if memory.status
            == MemoryStatus.ACTIVE
        ]

        # Keep the newest memories, then sort that window
        # chronologically for the LLM.
        remaining_active = sorted(
            remaining_active,
            key=lambda memory: (
                memory.updated_at
                or memory.created_at
                or datetime.min
            ),
            reverse=True,
        )

        memories = list(
            reversed(
                remaining_active[
                    : self.max_memories_per_run
                ]
            )
        )

        # -----------------------------------------------------
        # Not enough memories
        # -----------------------------------------------------

        if len(memories) < 2:
            if result.changed_memories:
                result.summary = (
                    "Expired memories archived automatically."
                )
            else:
                result.summary = (
                    "Not enough active memories to dream."
                )

            result.applied = bool(
                result.changed_memories
            )

            if not apply:
                result.changed_memories = []
                result.archive_operations = []

            return result

        # -----------------------------------------------------
        # LLM
        # -----------------------------------------------------

        prompt = self._build_prompt(
            memories
        )

        response = self.qwen.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=prompt,
            max_new_tokens=1024,
        )

        data = self._parse_response(
            response
        )

        (
            merges,
            archives,
            summary,
        ) = self._validate_operations(
            data=data,
            memories=memories,
        )

        result.merge_operations = merges

        result.archive_operations.extend(
            archives
        )

        if summary:
            result.summary = summary

        # -----------------------------------------------------
        # Preview mode
        # -----------------------------------------------------

        if not apply:
            result.changed_memories = []
            return result

        # -----------------------------------------------------
        # Apply operations
        # -----------------------------------------------------

        by_id = {
            memory.id: memory
            for memory in memories
        }

        changed = list(
            result.changed_memories
        )

        # -----------------------------------------------------
        # MERGES
        # -----------------------------------------------------

        for operation in merges:
            changed.extend(
                self._merge(
                    operation,
                    by_id,
                )
            )

        # -----------------------------------------------------
        # ARCHIVES
        # -----------------------------------------------------

        for operation in archives:
            for memory_id in operation.memory_ids:
                memory = by_id[
                    memory_id
                ]

                if (
                    memory.status
                    != MemoryStatus.ACTIVE
                ):
                    continue

                self._archive(
                    memory,
                    operation.reason,
                )

                changed.append(
                    memory
                )

        result.changed_memories = changed
        result.applied = bool(
            changed
        )

        return result

    # =========================================================
    # PUBLIC FORCE API
    # =========================================================

    def force_run(
        self,
        user_id: str,
    ) -> DreamingResult:
        """Force an immediate Dreaming pass and clear the pending counter."""

        self.reset_counter(
            user_id
        )

        return self.run(
            user_id=user_id,
            apply=True,
        )
