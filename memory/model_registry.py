from __future__ import annotations

from threading import Lock


class ModelRegistry:
    """One shared instance of every heavyweight model/component per process."""

    _lock = Lock()
    _qwen = None
    _retrieval_models = None
    _entity_enricher = None
    _grammar_normalizer = None

    @classmethod
    def get_qwen(cls):
        """Return the single shared Qwen3-4B instance for this process."""
        if cls._qwen is None:
            with cls._lock:
                if cls._qwen is None:
                    from qwen_model import QwenModel

                    print("Creating shared Qwen3-4B instance...")
                    cls._qwen = QwenModel()
        return cls._qwen

    @classmethod
    def get_retrieval_models(cls):
        """Return the single shared embedding/reranker manager."""
        if cls._retrieval_models is None:
            with cls._lock:
                if cls._retrieval_models is None:
                    from retrieval_model_manager import RetrievalModelManager

                    print("Creating shared retrieval model manager...")
                    cls._retrieval_models = RetrievalModelManager()
        return cls._retrieval_models

    @classmethod
    def get_entity_enricher(cls):
        """Return the single shared GLiNER entity enricher."""
        if cls._entity_enricher is None:
            with cls._lock:
                if cls._entity_enricher is None:
                    from entity_enricher import EntityEnricher

                    print("Creating shared GLiNER EntityEnricher instance...")
                    cls._entity_enricher = EntityEnricher()
        return cls._entity_enricher

    @classmethod
    def get_grammar_normalizer(cls):
        """Return the single shared Qwen3-0.6B grammar normalizer."""
        if cls._grammar_normalizer is None:
            with cls._lock:
                if cls._grammar_normalizer is None:
                    from memory_grammar_normalizer import MemoryGrammarNormalizer

                    print("Creating shared Qwen3-0.6B grammar normalizer...")
                    cls._grammar_normalizer = MemoryGrammarNormalizer()
        return cls._grammar_normalizer

    @classmethod
    def clear_runtime_cache(cls) -> None:
        """Drop registry references. Use only for an explicit full test reset."""
        cls._qwen = None
        cls._retrieval_models = None
        cls._entity_enricher = None
        cls._grammar_normalizer = None


def get_qwen():
    return ModelRegistry.get_qwen()


def get_retrieval_models():
    return ModelRegistry.get_retrieval_models()


def get_entity_enricher():
    return ModelRegistry.get_entity_enricher()


def get_grammar_normalizer():
    return ModelRegistry.get_grammar_normalizer()
