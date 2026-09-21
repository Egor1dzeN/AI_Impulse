from __future__ import annotations

import os
from threading import Lock

from cloud_ru_embedder import cloud_ru_embedding_configured
from cloud_ru_reranker import cloud_ru_rerank_configured
from gigachat_client import gigachat_is_configured

# The GigaChat personal tariff (GIGACHAT_API_PERS) was found to return 402
# Payment Required on every /v1/embeddings model (Embeddings, Embeddings-2,
# EmbeddingsGigaR, GigaEmbeddings-3B-2025-09) — client.get_balance() only
# lists quota for chat models, none for embeddings. So that embedder switch
# is opt-in behind its own flag rather than tied to GIGA_CHAT_IDE, to avoid
# turning memory search into a hard failure on accounts without paid
# embeddings access. Flip this on once GigaChat embeddings billing is set
# up — until then, GIGA_CHAT_EMBEDDING (cloud.ru, checked first below) is
# the working API-backed embedder.
GIGACHAT_USE_EMBEDDINGS = os.environ.get("GIGACHAT_USE_EMBEDDINGS", "0") == "1"


class ModelRegistry:
    """One shared instance of every heavyweight model/component per process."""

    _lock = Lock()
    _qwen = None
    _retrieval_models = None
    _entity_enricher = None
    _grammar_normalizer = None

    @classmethod
    def get_qwen(cls):
        """Return the single shared text-generation model used by memory
        extraction/coreference/relation-extraction/dreaming/validation:
        GigaChat-backed when GIGA_CHAT_IDE is set, else the local
        Qwen3-4B."""
        if cls._qwen is None:
            with cls._lock:
                if cls._qwen is None:
                    if gigachat_is_configured():
                        from gigachat_text_model import GigaChatTextModel

                        cls._qwen = GigaChatTextModel()
                    else:
                        from qwen_model import QwenModel

                        print("Creating shared Qwen3-4B instance...")
                        cls._qwen = QwenModel()
        return cls._qwen

    @classmethod
    def get_retrieval_models(cls):
        """Return the single shared embedding/reranker manager.

        Embedder precedence: cloud.ru's hosted Qwen3-Embedding-0.6B
        (GIGA_CHAT_EMBEDDING) first, then GigaChat's own /embeddings if
        explicitly opted into (GIGACHAT_USE_EMBEDDINGS, see comment above —
        needs separate paid access), else the local Qwen3-Embedding-0.6B.

        Reranker: cloud.ru's hosted Qwen3-Reranker-0.6B (same
        GIGA_CHAT_EMBEDDING key, via its /score endpoint) if configured,
        else the local Qwen3-Reranker-0.6B — GigaChat itself has no
        reranking endpoint at all."""
        if cls._retrieval_models is None:
            with cls._lock:
                if cls._retrieval_models is None:
                    from retrieval_model_manager import RetrievalModelManager

                    embedder = None
                    if cloud_ru_embedding_configured():
                        from cloud_ru_embedder import CloudRuEmbedder

                        embedder = CloudRuEmbedder()
                    elif gigachat_is_configured() and GIGACHAT_USE_EMBEDDINGS:
                        from gigachat_embedder import GigaChatEmbedder

                        embedder = GigaChatEmbedder()

                    reranker = None
                    if cloud_ru_rerank_configured():
                        from cloud_ru_reranker import CloudRuReranker

                        reranker = CloudRuReranker()

                    if embedder is None and reranker is None:
                        print("Creating shared retrieval model manager...")
                    cls._retrieval_models = RetrievalModelManager(embedder=embedder, reranker=reranker)
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
        """Return the single shared grammar normalizer: GigaChat-backed
        when GIGA_CHAT_IDE is set, else the local Qwen3-0.6B."""
        if cls._grammar_normalizer is None:
            with cls._lock:
                if cls._grammar_normalizer is None:
                    if gigachat_is_configured():
                        from gigachat_grammar_normalizer import GigaChatGrammarNormalizer

                        cls._grammar_normalizer = GigaChatGrammarNormalizer()
                    else:
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
