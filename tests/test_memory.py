"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

tests/test_memory.py — Tests del MemoryStore
"""

import pytest
from clawlite.memory.store import MemoryStore
from clawlite.memory.embeddings import EmbeddingEngine

USER_ID = "test_user_123"


@pytest.fixture
def store(tmp_path):
    # DB en directorio temporal aislado por test: sin borrado manual (el lock de
    # SQLite en Windows lo impedía) y sin contaminar ./data/ con DBs de prueba.
    s = MemoryStore(db_path=str(tmp_path / "test_clawlite.db"),
                    embedding_engine=EmbeddingEngine())
    yield s


def test_save_and_retrieve(store):
    store.save_message(USER_ID, "user", "Hello ClawLite")
    store.save_message(USER_ID, "assistant", "Hello! How can I help?")

    recent = store.get_recent(USER_ID, limit=10)
    assert len(recent) == 2
    assert recent[0]["role"] == "user"
    assert recent[1]["role"] == "assistant"


def test_recall_similar(store):
    store.save_message(USER_ID, "user", "I love coffee in the morning")
    store.save_message(USER_ID, "user", "My favorite sport is tennis")
    store.save_message(USER_ID, "user", "I work as a software engineer")

    results = store.recall_similar(USER_ID, "what do I drink?", top_k=1)
    assert len(results) >= 1
    assert "coffee" in results[0].lower()


def test_clear_user(store):
    store.save_message(USER_ID, "user", "This should be deleted")
    store.clear_user(USER_ID)

    recent = store.get_recent(USER_ID)
    assert len(recent) == 0


def test_empty_recall_returns_empty(store):
    results = store.recall_similar("nonexistent_user", "anything", top_k=5)
    assert results == []
