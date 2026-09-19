from __future__ import annotations

import os
from dataclasses import dataclass

import torch
from gliner import GLiNER


@dataclass
class Entity:
    text: str
    label: str
    score: float
    start: int
    end: int


class EntityEnricher:
    """GLiNER entity enricher.

    Uses FP16 on CUDA rather than INT8.

    GLiNER documentation notes that INT8 can reduce accuracy for stock
    DeBERTa-based models unless the model was trained with QAT. FP16 is used
    here as the safer memory reduction.

    Runs on CPU by default: it only feeds the background memory-write
    pipeline (never the interactive chat path), so its latency matters far
    less than leaving GPU VRAM free for the chat model — important on
    smaller cards where five resident models (chat LLM + embedder +
    reranker + GLiNER + grammar normalizer) can otherwise leave too little
    VRAM for the chat model's own generation and make it fail silently.
    Set GLINER_DEVICE=cuda to put it back on GPU if you have room to spare.
    """

    MODEL_NAME = "urchade/gliner_multi-v2.1"
    USE_FP16 = True
    DEFAULT_DEVICE = os.getenv("GLINER_DEVICE", "cpu").strip().lower()

    LABELS_BY_MEMORY_TYPE = {
        "fact": [
            "person",
            "location",
            "organization",
            "company",
            "product",
            "profession",
            "date",
            "time",
        ],
        "preference": [
            "person",
            "location",
            "organization",
            "company",
            "product",
            "food",
            "activity",
        ],
        "episode": [
            "person",
            "location",
            "organization",
            "company",
            "product",
            "date",
            "time",
            "event",
        ],
        "entity": [
            "person",
            "location",
            "organization",
            "company",
            "product",
        ],
        "relationship": [
            "person",
            "organization",
            "company",
            "location",
        ],
        "goal": [
            "person",
            "location",
            "organization",
            "company",
            "product",
            "activity",
            "date",
            "time",
        ],
        "state": [
            "person",
            "location",
            "organization",
            "company",
            "product",
            "profession",
            "date",
            "time",
        ],
        "procedure": [
            "person",
            "organization",
            "company",
            "product",
            "activity",
        ],
    }

    ALL_LABELS = sorted({
        label
        for labels in LABELS_BY_MEMORY_TYPE.values()
        for label in labels
    })

    PRONOUNS_AND_GENERIC = {
        "я",
        "мы",
        "ты",
        "вы",
        "он",
        "она",
        "оно",
        "они",
        "мой",
        "моя",
        "моё",
        "мое",
        "мои",
        "меня",
        "мне",
        "мной",
        "ему",
        "ей",
        "их",
        "его",
        "ее",
        "её",
        "этот",
        "эта",
        "это",
        "эти",
        "у меня",
        "у нас",
        "сейчас",
        "теперь",
    }

    def __init__(
        self,
        threshold: float = 0.5,
        device: str | None = None,
    ) -> None:
        self.threshold = threshold
        requested = device or self.DEFAULT_DEVICE
        self.device = (
            requested
            if requested != "cuda" or torch.cuda.is_available()
            else "cpu"
        )
        self.fp16 = (
            self.device.startswith("cuda")
            and self.USE_FP16
        )

        print(
            f"Loading {self.MODEL_NAME}..."
        )

        kwargs = {
            "map_location": self.device,
        }

        if self.fp16:
            kwargs["dtype"] = torch.float16

        try:
            self.model = GLiNER.from_pretrained(
                self.MODEL_NAME,
                **kwargs,
            )
        except TypeError:
            self.model = GLiNER.from_pretrained(
                self.MODEL_NAME
            )

            try:
                self.model.to(self.device)
            except AttributeError:
                pass

            if self.fp16:
                try:
                    self.model.to(
                        dtype=torch.float16
                    )
                except (
                    AttributeError,
                    RuntimeError,
                ):
                    pass

        if self.fp16:
            print(
                "GLiNER multi-v2.1: FP16 GPU"
            )

    def _get_labels(
        self,
        memory_type,
    ) -> list[str]:
        if memory_type is None:
            return self.ALL_LABELS

        memory_type_value = getattr(
            memory_type,
            "value",
            str(memory_type),
        )

        return self.LABELS_BY_MEMORY_TYPE.get(
            memory_type_value,
            self.ALL_LABELS,
        )

    def extract_entities(
        self,
        text: str,
        memory_type=None,
    ) -> list[Entity]:
        if not text.strip():
            return []

        labels = self._get_labels(
            memory_type
        )

        entities = self.model.predict_entities(
            text,
            labels,
            threshold=self.threshold,
        )

        result: list[Entity] = []

        for entity in entities:
            entity_text = entity.get(
                "text",
                "",
            ).strip()

            if not entity_text:
                continue

            if (
                entity_text.lower()
                in self.PRONOUNS_AND_GENERIC
            ):
                continue

            if len(entity_text) < 2:
                continue

            result.append(
                Entity(
                    text=entity_text,
                    label=entity.get(
                        "label",
                        "unknown",
                    ),
                    score=float(
                        entity.get(
                            "score",
                            0.0,
                        )
                    ),
                    start=int(
                        entity.get(
                            "start",
                            -1,
                        )
                    ),
                    end=int(
                        entity.get(
                            "end",
                            -1,
                        )
                    ),
                )
            )

        return result
