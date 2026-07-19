"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

tests/test_agent.py — Tests del Agent core (contrato actual)
Mockea la capa LLM completa: el planner (salida estructurada) y la generación
conversacional. Sin Ollama/Groq/Tavily. Async vía asyncio.run — sin depender
del plugin pytest-asyncio (no está en el venv del proyecto).
"""

import asyncio
from unittest.mock import patch

import pytest

from clawlite.agent.core import Agent
from clawlite.memory.store import MemoryStore
from clawlite.memory.embeddings import EmbeddingEngine
from clawlite.memory.profile import DeepMemory

USER_ID = "agent_test_user"

PLANNER_JSON = (
    '{"tool": "direct_answer", "is_news": false, "user_asserts": false, "lang": "en"}'
)


def make_fake_llm(reply_text: str, source: str):
    """complete() falso fiel al contrato real: el planner y las extracciones
    piden structured=True (devuelven JSON); la generación conversacional no
    (devuelve texto). El planner se distingue por task_type."""
    async def fake_complete(**kwargs):
        if kwargs.get("structured"):
            if kwargs.get("task_type") == "planner":
                return PLANNER_JSON, "local"
            return "{}", "local"  # extracción de perfil y afines: sin hallazgos
        return reply_text, source
    return fake_complete


@pytest.fixture
def agent(tmp_path):
    engine = EmbeddingEngine()
    memory = MemoryStore(db_path=str(tmp_path / "agent.db"), embedding_engine=engine)
    deep = DeepMemory(db_path=str(tmp_path / "deep.db"))
    return Agent(memory=memory, deep_memory=deep), memory


def test_agent_returns_response(agent):
    ag, _ = agent
    with patch("clawlite.agent.core.llm") as mock_llm:
        mock_llm.complete = make_fake_llm("Hello from mock LLM", "local")
        response, used_cloud = asyncio.run(ag.handle(USER_ID, "Hello"))
    assert isinstance(response, str)
    assert len(response) > 0
    assert used_cloud is False


def test_agent_flags_cloud_usage(agent):
    ag, _ = agent
    with patch("clawlite.agent.core.llm") as mock_llm:
        mock_llm.complete = make_fake_llm("Cloud response", "cloud")
        _, used_cloud = asyncio.run(ag.handle(USER_ID, "Tell me something"))
    assert used_cloud is True


def test_agent_saves_to_memory(agent):
    ag, memory = agent
    with patch("clawlite.agent.core.llm") as mock_llm:
        mock_llm.complete = make_fake_llm("Nice to meet you, Alex", "local")
        asyncio.run(ag.handle(USER_ID, "My name is Alex"))
    recent = memory.get_recent(USER_ID)
    assert any(m["role"] == "user" and "Alex" in m["content"] for m in recent)
    assert any(m["role"] == "assistant" for m in recent)
    # Procedencia (victoria D2-2): un turno conversacional queda como 'chat'.
    assert all(m["kind"] == "chat" for m in recent)
