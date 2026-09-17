from __future__ import annotations

import functools
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import torch


@dataclass
class ProfileRecord:
    name: str
    seconds: float
    gpu_before: float
    gpu_after: float


@dataclass
class MemoryProfiler:
    records: list[ProfileRecord] = field(default_factory=list)

    @staticmethod
    def gpu_allocated_gb() -> float:
        if not torch.cuda.is_available():
            return 0.0
        torch.cuda.synchronize()
        return torch.cuda.memory_allocated() / 1024**3

    def measure(
        self,
        name: str,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if torch.cuda.is_available():
            torch.cuda.synchronize()

        before = self.gpu_allocated_gb()
        start = time.perf_counter()

        try:
            return fn(*args, **kwargs)
        finally:
            if torch.cuda.is_available():
                torch.cuda.synchronize()

            elapsed = time.perf_counter() - start
            after = self.gpu_allocated_gb()

            self.records.append(
                ProfileRecord(
                    name=name,
                    seconds=elapsed,
                    gpu_before=before,
                    gpu_after=after,
                )
            )

    def wrap(
        self,
        owner: Any,
        attribute: str,
        name: str | None = None,
    ) -> None:
        original = getattr(owner, attribute)

        if getattr(original, "_memory_profiler_wrapped", False):
            return

        label = name or attribute

        @functools.wraps(original)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            return self.measure(
                label,
                original,
                *args,
                **kwargs,
            )

        wrapped._memory_profiler_wrapped = True
        setattr(owner, attribute, wrapped)

    def clear(self) -> None:
        self.records.clear()

    def print_report(self) -> None:
        print("\n" + "=" * 100)
        print("MEMORY PROFILER")
        print("=" * 100)

        if not self.records:
            print("No profile records.")
            return

        total = sum(record.seconds for record in self.records)

        for record in self.records:
            share = 100.0 * record.seconds / total if total else 0.0
            delta = record.gpu_after - record.gpu_before

            print(
                f"{record.name:<30} "
                f"{record.seconds:>9.3f}s "
                f"{share:>6.1f}% "
                f"GPU {record.gpu_before:>5.2f}"
                f" -> {record.gpu_after:>5.2f} GB "
                f"({delta:+.2f})"
            )

        print("-" * 100)
        print(f"{'TOTAL':<30} {total:>9.3f}s")


def instrument_memory_system(system) -> MemoryProfiler:
    profiler = MemoryProfiler()

    pipeline = system.pipeline
    enricher = pipeline.enricher

    profiler.wrap(enricher, "process", "enricher.process")
    profiler.wrap(pipeline.matcher, "find_matches", "matcher.find_matches")
    profiler.wrap(pipeline.consolidator, "decide", "consolidator.decide")
    profiler.wrap(pipeline.updater, "apply", "updater.apply")

    profiler.wrap(enricher.extractor, "extract", "extractor.extract")

    validator = getattr(enricher, "validator", None)
    if validator is not None:
        profiler.wrap(validator, "validate", "validator.validate")

    grammar = getattr(enricher, "grammar_normalizer", None)
    if grammar is not None:
        if hasattr(grammar, "correct_many"):
            profiler.wrap(grammar, "correct_many", "grammar.correct_many")
        elif hasattr(grammar, "correct"):
            profiler.wrap(grammar, "correct", "grammar.correct")

    profiler.wrap(
        enricher.coreference_resolver,
        "resolve",
        "coreference.resolve",
    )

    profiler.wrap(
        enricher.temporal_enricher,
        "extract",
        "temporal.extract",
    )

    profiler.wrap(
        enricher.entity_enricher,
        "extract_entities",
        "entities.extract_entities",
    )

    profiler.wrap(
        enricher.relation_extractor,
        "extract",
        "relations.extract",
    )

    return profiler
