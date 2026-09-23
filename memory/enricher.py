from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
import uuid

from models import MemoryCandidate, MemoryType, Source
from extractor_contextual import MemoryExtractor
from memory_validator import MemoryValidator
from memory_grammar_normalizer import MemoryGrammarNormalizer
from entity_enricher import EntityEnricher, Entity
from coreference import CoreferenceResolver, CoreferenceCluster
from temporal import TemporalEnricher, TemporalExpression
from candidate_grounder import CandidateGrounder, GroundedCandidate
from subject_resolver import SubjectResolver, ResolvedSubject
from relation_extractor import RelationExtractor, Relation
from relation_linker import RelationLinker
from memory_context import MemoryContext
from candidate_router import CandidateRoute, CandidateRouter
from unified_memory_analyzer import UnifiedMemoryAnalyzer


@dataclass
class EnrichedMemoryCandidate:
    candidate: MemoryCandidate
    grounded: GroundedCandidate | None = None
    entities: list[Entity] = field(default_factory=list)
    coreference_clusters: list[CoreferenceCluster] = field(default_factory=list)
    temporal_expressions: list[TemporalExpression] = field(default_factory=list)
    subject: ResolvedSubject | None = None
    relations: list[Relation] = field(default_factory=list)


_CHUNK_HEADER = "Переписка в Telegram"
_USER_MARK = "(пользователь)"
_PROPER_RE = re.compile(r"\b[А-ЯЁA-Z][а-яёa-z]{2,}")
_NOT_PROPER = {"Пользователь", "Пользователя", "Пользователю", "Пользователем", "Пользователе", "Telegram"}


def _drop_speaker_misattributions(text: str, candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
    """In a chat excerpt, a fact about the user built on a name that only the
    OTHER person said is that person's fact: brother "Я в Москве совсем
    закрутился" became "Пользователь живёт в Москве" even with the prompt
    telling the model not to. Deterministic backstop: a "Пользователь …"
    candidate whose proper nouns (cities, places) appear only in other
    speakers' lines — never in the user's own — is dropped."""
    if not text.startswith(_CHUNK_HEADER) or _USER_MARK not in text:
        return candidates
    own_lines, other_lines = [], []
    for line in text.split("\n")[1:]:
        speaker, _, said = line.partition(":")
        (own_lines if _USER_MARK in speaker else other_lines).append(said.lower())
    own, other = " ".join(own_lines), " ".join(other_lines)
    kept = []
    for c in candidates:
        content = c.content or ""
        if not content.lower().startswith("пользовател"):
            kept.append(c)
            continue
        names = [w for w in _PROPER_RE.findall(content) if w not in _NOT_PROPER]
        stems = [w.lower()[: max(4, len(w) - 2)] for w in names]
        only_other = [s for s in stems if s in other and s not in own]
        if only_other:
            print(f"[ENRICHER] dropped misattributed candidate: {content!r} ({only_other} said only by others)")
            continue
        kept.append(c)
    return kept


class MemoryEnricher:
    """Unified memory enrichment with one expensive LLM call."""

    PRONOUN_PATTERN = re.compile(
        r"\b(он|она|они|его|её|ее|ему|ей|им|ним|ней|них|него|неё)\b",
        re.IGNORECASE,
    )

    COMPLEX_RELATION_PATTERN = re.compile(
        r"\b(мой|моя|мои|брат|сестра|коллег|друг|отец|мать|муж|жена)\b",
        re.IGNORECASE,
    )

    def __init__(
        self,
        extractor: MemoryExtractor,
        entity_enricher: EntityEnricher,
        coreference_resolver: CoreferenceResolver,
        temporal_enricher: TemporalEnricher,
        grounder: CandidateGrounder,
        subject_resolver: SubjectResolver,
        relation_extractor: RelationExtractor,
        relation_linker: RelationLinker,
        validator: MemoryValidator | None = None,
        grammar_normalizer: MemoryGrammarNormalizer | None = None,
        unified_analyzer: UnifiedMemoryAnalyzer | None = None,
    ) -> None:
        self.extractor = extractor
        self.entity_enricher = entity_enricher
        self.validator = validator
        self.grammar_normalizer = grammar_normalizer
        self.coreference_resolver = coreference_resolver
        self.temporal_enricher = temporal_enricher
        self.grounder = grounder
        self.subject_resolver = subject_resolver
        self.relation_extractor = relation_extractor
        self.relation_linker = relation_linker
        self.unified_analyzer = unified_analyzer

    @staticmethod
    def _needs_coreference(text: str) -> bool:
        return bool(MemoryEnricher.PRONOUN_PATTERN.search(text))

    @staticmethod
    def _needs_entity_enrichment(
        candidates: list[MemoryCandidate],
        text: str,
    ) -> bool:
        if any(
            candidate.type in {
                MemoryType.ENTITY,
                MemoryType.RELATIONSHIP,
            }
            for candidate in candidates
        ):
            return True

        return bool(
            MemoryEnricher.COMPLEX_RELATION_PATTERN.search(text)
        )

    @staticmethod
    def _relation_from_dict(
        item: dict,
        source_text: str,
        fallback_confidence: float,
    ) -> Relation | None:
        subject = str(item.get("subject") or "").strip()
        predicate = str(item.get("predicate") or "").strip()
        object_value = str(item.get("object") or "").strip()

        if not subject or not predicate or not object_value:
            return None

        try:
            confidence = float(
                item.get("confidence", fallback_confidence)
            )
        except (TypeError, ValueError):
            confidence = fallback_confidence

        confidence = max(0.0, min(1.0, confidence))

        source_fragment = str(
            item.get("source_fragment") or source_text
        ).strip()

        return Relation(
            id=str(uuid.uuid4()),
            subject=subject,
            predicate=re.sub(
                r"\s+",
                "_",
                predicate.lower(),
            ),
            object=object_value,
            confidence=confidence,
            temporal_value=None,
            temporal_text=None,
            source_text=source_text,
            source_fragment=source_fragment,
            metadata={"relation_source": "unified_llm"},
        )

    @classmethod
    def _candidate_relations(
        cls,
        candidate: MemoryCandidate,
    ) -> list[Relation]:
        result: list[Relation] = []

        raw_relations = (
            candidate.metadata or {}
        ).get("relations", [])

        if not isinstance(raw_relations, list):
            return result

        for item in raw_relations:
            if not isinstance(item, dict):
                continue

            relation = cls._relation_from_dict(
                item=item,
                source_text=candidate.source_text,
                fallback_confidence=candidate.confidence,
            )
            if relation is not None:
                result.append(relation)

        return result

    @staticmethod
    def _relation_matches_candidate(
        relation: Relation,
        candidate: MemoryCandidate,
    ) -> bool:
        source = candidate.source_text.lower()
        fragment = (relation.source_fragment or "").lower()
        content = candidate.content.lower()

        if fragment and fragment in source:
            fragment_tokens = set(
                re.findall(r"[a-zа-яё0-9]+", fragment)
            )
            content_tokens = set(
                re.findall(r"[a-zа-яё0-9]+", content)
            )
            if fragment_tokens & content_tokens:
                return True

        relation_tokens = set(
            re.findall(
                r"[a-zа-яё0-9]+",
                f"{relation.subject} {relation.object} {relation.predicate}",
            )
        )
        content_tokens = set(
            re.findall(r"[a-zа-яё0-9]+", content)
        )

        return len(relation_tokens & content_tokens) >= 1

    def _attach_top_level_relations(
        self,
        candidates: list[MemoryCandidate],
        raw_relations: list[dict],
    ) -> None:
        if not raw_relations:
            return

        relation_objects: list[Relation] = []

        for item in raw_relations:
            if not isinstance(item, dict):
                continue

            relation = self._relation_from_dict(
                item=item,
                source_text=item.get("source_text", ""),
                fallback_confidence=0.8,
            )
            if relation is not None:
                relation_objects.append(relation)

        for relation in relation_objects:
            for candidate in candidates:
                if self._relation_matches_candidate(
                    relation,
                    candidate,
                ):
                    metadata = candidate.metadata
                    relations = metadata.setdefault(
                        "relations",
                        [],
                    )
                    relations.append({
                        "subject": relation.subject,
                        "predicate": relation.predicate,
                        "object": relation.object,
                        "source_fragment": relation.source_fragment,
                        "confidence": relation.confidence,
                    })
                    break

    def _legacy_process(
        self,
        text: str,
        user_id: str,
        context: MemoryContext | None,
        source: Source | None = None,
    ) -> tuple[list[MemoryCandidate], bool]:
        candidates = self.extractor.extract(
            text=text,
            user_id=user_id,
            context=context,
            source=source,
        )

        if not candidates:
            return [], False

        routes = {
            candidate.id: CandidateRouter.route(candidate)
            for candidate in candidates
        }

        full_candidates = [
            candidate
            for candidate in candidates
            if routes[candidate.id] == CandidateRoute.FULL
        ]

        if full_candidates and self.validator is not None:
            validation_results = self.validator.validate(
                candidates=full_candidates,
                source_text=text,
            )

            validated = {
                result.candidate.id: result.candidate
                for result in validation_results
                if result.keep
            }

            candidates = [
                candidate
                for candidate in candidates
                if (
                    routes[candidate.id] == CandidateRoute.FAST
                    or candidate.id in validated
                )
            ]

            for index, candidate in enumerate(candidates):
                if candidate.id in validated:
                    candidates[index] = validated[candidate.id]

        return candidates, bool(full_candidates)

    def process(
        self,
        text: str,
        user_id: str,
        context: MemoryContext | None = None,
        source: Source | None = None,
    ) -> list[EnrichedMemoryCandidate]:
        """
        Main enrichment path.

        route_mode:
            "fast"    -> deterministic result from UnifiedMemoryAnalyzer
            "unified" -> one Qwen3-4B unified analysis
            "legacy"  -> old extractor pipeline (fallback only)

        `source` identifies where `text` came from (email/telegram/
        calendar/chat/...) and, via its `created_at`, when the underlying
        event happened — as opposed to when this pipeline run processed it.
        Threaded all the way down into extraction so every resulting Memory
        records a real source and validity period instead of defaulting to
        a bare "text" source stamped with the ingestion time (see
        memory_updater.py's _create_memory/_conflict, which prefer
        source.created_at over utcnow() for valid_from).
        """

        route_mode = "legacy"
        unified = None

        # ---------------------------------------------------------
        # 1. EXTRACTION / ROUTING
        # ---------------------------------------------------------
        if self.unified_analyzer is not None:
            try:
                unified = self.unified_analyzer.analyze(
                    text=text,
                    user_id=user_id,
                    context=context,
                    source=source,
                )

                candidates = unified.candidates

                if unified.used_llm:
                    route_mode = "unified"
                else:
                    route_mode = "fast"

                print(
                    f"[ENRICHER] route={route_mode}"
                )

            except Exception as exc:
                print(
                    f"[UNIFIED] analyzer error: {exc}"
                )
                print(
                    "[UNIFIED] switching to legacy path"
                )

                candidates, _ = self._legacy_process(
                    text=text,
                    user_id=user_id,
                    context=context,
                    source=source,
                )

                route_mode = "legacy"
        else:
            candidates, _ = self._legacy_process(
                text=text,
                user_id=user_id,
                context=context,
                source=source,
            )

            route_mode = "legacy"

        candidates = _drop_speaker_misattributions(text, candidates)

        if not candidates:
            return []

        # ---------------------------------------------------------
        # 1.5 GOAL COMPLETION FAST-PATH
        # ---------------------------------------------------------
        # Completion messages are handled deterministically. The router
        # already classifies messages such as "Отчёт я уже закончил."
        # as FAST, so the extracted candidate must be marked explicitly
        # before MemoryMatcher / MemoryConsolidator receive it.
        #
        # No extra Qwen call is performed here.
        completion_pattern = re.compile(
            r"\b("
            r"закончил(?:а|о|и)?|"
            r"завершил(?:а|о|и)?|"
            r"выполнил(?:а|о|и)?|"
            r"сделал(?:а|о|и)?"
            r")\b",
            re.IGNORECASE,
        )

        if (
            route_mode == "fast"
            and completion_pattern.search(text)
        ):
            for candidate in candidates:
                candidate.metadata = (
                    candidate.metadata or {}
                )

                candidate.metadata[
                    "event_type"
                ] = "goal_completion"

                candidate.metadata[
                    "completed"
                ] = True

                # Preserve an explicit target when the upstream
                # extractor already supplied one. Otherwise use the
                # candidate content for matcher fallback.
                target_goal = str(
                    candidate.metadata.get(
                        "target_goal"
                    )
                    or candidate.content
                    or ""
                ).strip()

                if target_goal:
                    candidate.metadata[
                        "target_goal"
                    ] = target_goal

                candidate.type = MemoryType.STATE

            print(
                "[GOAL COMPLETION] "
                f"marked {len(candidates)} candidate(s)"
            )

        # ---------------------------------------------------------
        # 2. RELATIONS FROM UNIFIED ANALYZER
        # ---------------------------------------------------------
        if route_mode == "unified" and unified is not None:
            raw_relations = getattr(
                unified,
                "relations",
                [],
            ) or []

            existing_relationship_keys: set[
                tuple[str, str, str]
            ] = set()

            for candidate in candidates:
                if (
                    candidate.type
                    != MemoryType.RELATIONSHIP
                ):
                    continue

                relation_data = (
                    candidate.metadata or {}
                ).get("relation")

                if not isinstance(
                    relation_data,
                    dict,
                ):
                    continue

                key = (
                    str(
                        relation_data.get("subject")
                        or ""
                    ).strip().lower(),
                    str(
                        relation_data.get("predicate")
                        or ""
                    ).strip().lower(),
                    str(
                        relation_data.get("object")
                        or ""
                    ).strip().lower(),
                )

                if all(key):
                    existing_relationship_keys.add(
                        key
                    )

            relation_words = {
                "быть_братом": "брат",
                "брат": "брат",
                "быть_сестрой": "сестра",
                "сестра": "сестра",
                "быть_мужем": "муж",
                "муж": "муж",
                "быть_женой": "жена",
                "жена": "жена",
                "быть_отцом": "отец",
                "отец": "отец",
                "быть_матерью": "мать",
                "мать": "мать",
                "быть_другом": "друг",
                "друг": "друг",
                "быть_коллегой": "коллега",
                "коллега": "коллега",
            }

            for raw_relation in raw_relations:
                if not isinstance(
                    raw_relation,
                    dict,
                ):
                    continue

                subject = str(
                    raw_relation.get("subject")
                    or ""
                ).strip()

                predicate = str(
                    raw_relation.get("predicate")
                    or ""
                ).strip().lower()

                object_value = str(
                    raw_relation.get("object")
                    or ""
                ).strip()

                source_fragment = str(
                    raw_relation.get(
                        "source_fragment"
                    )
                    or ""
                ).strip()

                if (
                    not subject
                    or not predicate
                    or not object_value
                ):
                    continue

                # Relation must be grounded in the current message.
                if (
                    source_fragment
                    and source_fragment.lower()
                    not in text.lower()
                ):
                    continue

                # Only actual semantic relationships become
                # relationship memories. Actions do not.
                relation_word = relation_words.get(
                    predicate
                )

                if relation_word is None:
                    continue

                key = (
                    subject.lower(),
                    predicate,
                    object_value.lower(),
                )

                if key in existing_relationship_keys:
                    continue

                confidence = raw_relation.get(
                    "confidence",
                    0.9,
                )

                try:
                    confidence = float(
                        confidence
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    confidence = 0.9

                confidence = max(
                    0.0,
                    min(1.0, confidence),
                )

                source = (
                    candidates[0].source
                    if candidates
                    else Source(
                        type="text",
                        id=str(uuid.uuid4()),
                    )
                )

                candidates.append(
                    MemoryCandidate(
                        id=str(uuid.uuid4()),
                        user_id=user_id,
                        type=MemoryType.RELATIONSHIP,
                        content=(
                            f"{subject} — "
                            f"{relation_word} "
                            f"{object_value}"
                        ),
                        confidence=confidence,
                        source=source,
                        created_at=datetime.utcnow(),
                        source_text=text,
                        metadata={
                            "extraction_source": "unified_llm",
                            "relation": raw_relation,
                        },
                    )
                )

                existing_relationship_keys.add(key)

        # ---------------------------------------------------------
        # 3. ROUTE EACH CANDIDATE
        # ---------------------------------------------------------
        if route_mode == "unified":
            routes = {
                candidate.id: CandidateRoute.FULL
                for candidate in candidates
            }
        else:
            routes = {
                candidate.id: CandidateRoute.FAST
                for candidate in candidates
            }

        full_candidates = [
            candidate
            for candidate in candidates
            if routes[candidate.id]
            == CandidateRoute.FULL
        ]

        # ---------------------------------------------------------
        # 4. LEGACY VALIDATOR / GRAMMAR
        # ---------------------------------------------------------
        if (
            route_mode == "legacy"
            and full_candidates
            and self.validator is not None
        ):
            validation_results = (
                self.validator.validate(
                    candidates=full_candidates,
                    source_text=text,
                )
            )

            validated = {
                result.candidate.id:
                    result.candidate
                for result in validation_results
                if result.keep
            }

            candidates = [
                candidate
                for candidate in candidates
                if (
                    routes[candidate.id]
                    == CandidateRoute.FAST
                    or candidate.id in validated
                )
            ]

            for index, candidate in enumerate(
                candidates
            ):
                if candidate.id in validated:
                    candidates[index] = validated[
                        candidate.id
                    ]

            if not candidates:
                return []

            routes = {
                candidate.id: CandidateRouter.route(
                    candidate
                )
                for candidate in candidates
            }

            full_candidates = [
                candidate
                for candidate in candidates
                if routes[candidate.id]
                == CandidateRoute.FULL
            ]

        # Grammar normalization is intentionally one batch call.
        if (
            self.grammar_normalizer is not None
            and candidates
        ):
            corrections = (
                self.grammar_normalizer.correct_many(
                    [
                        candidate.content
                        for candidate in candidates
                    ]
                )
            )

            for candidate, corrected in zip(
                candidates,
                corrections,
            ):
                if corrected and corrected.strip():
                    candidate.content = (
                        corrected.strip()
                    )

            print(
                f"[GRAMMAR] normalized "
                f"{len(candidates)} candidate(s)"
            )

        # ---------------------------------------------------------
        # 5. SOURCE-LEVEL ENRICHMENT
        # ---------------------------------------------------------
        need_coreference = (
            route_mode == "legacy"
            and bool(full_candidates)
            and self._needs_coreference(text)
        )

        coreference_clusters = (
            self.coreference_resolver.resolve(
                text
            )
            if need_coreference
            else []
        )

        temporal_expressions = (
            self.temporal_enricher.extract(text)
        )

        need_entities = (
            self._needs_entity_enrichment(
                candidates,
                text,
            )
        )

        all_entities: list[Entity] = []

        if need_entities:
            all_entities = (
                self.entity_enricher.extract_entities(
                    text=text,
                    memory_type=None,
                )
            )

        # ---------------------------------------------------------
        # 6. BUILD ENRICHED RESULTS
        # ---------------------------------------------------------
        results: list[
            EnrichedMemoryCandidate
        ] = []

        for candidate in candidates:
            grounded = self.grounder.ground(
                candidate=candidate,
                entities=all_entities,
            )

            subject = self.subject_resolver.resolve(
                candidate=candidate,
                grounded=grounded,
                entities=all_entities,
                coreference_clusters=(
                    coreference_clusters
                ),
                context=context,
            )

            relations = (
                self._candidate_relations(
                    candidate
                )
            )

            # Separate legacy relation extraction
            # is used only when the legacy pipeline
            # actually produced a FULL candidate.
            if (
                route_mode == "legacy"
                and routes[candidate.id]
                == CandidateRoute.FULL
            ):
                legacy_relations = (
                    self.relation_extractor.extract(
                        source_text=text,
                        entities=all_entities,
                        coreference_clusters=(
                            coreference_clusters
                        ),
                        temporal_expressions=(
                            temporal_expressions
                        ),
                    )
                )

                for relation in legacy_relations:
                    linked = (
                        self.relation_linker.link(
                            relation=relation,
                            candidate=candidate,
                            grounded=grounded,
                            coreference_clusters=(
                                coreference_clusters
                            ),
                            entities=all_entities,
                        )
                    )

                    if linked is not None:
                        relations.append(
                            linked.relation
                        )

            results.append(
                EnrichedMemoryCandidate(
                    candidate=candidate,
                    grounded=grounded,
                    entities=all_entities,
                    coreference_clusters=(
                        coreference_clusters
                    ),
                    temporal_expressions=(
                        temporal_expressions
                    ),
                    subject=subject,
                    relations=relations,
                )
            )

        return results

