"""Fork: on-demand conversation translation endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


async def _register(client, username="translator"):
    r = await client.post(
        "/api/auth/register",
        json={
            "username": username,
            "display_name": "T",
            "email": f"{username}@example.com",
            "password": "Passw0rd!123",
            "native_language": "en",
            "accept_terms": True,
        },
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_translate_requires_auth(client):
    r = await client.post("/api/translate", json={"text": "hola"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_translate_returns_the_llm_translation(client):
    headers = await _register(client)
    with patch(
        "app.routers.translation.llm_adapter.chat",
        new=AsyncMock(return_value="  hello  "),
    ) as mock_chat:
        r = await client.post(
            "/api/translate", json={"text": "hola"}, headers=headers
        )
    assert r.status_code == 200
    assert r.json() == {"translation": "hello"}
    messages = mock_chat.await_args.args[0]
    assert messages[-1] == {"role": "user", "content": "hola"}
    assert "English" in messages[0]["content"]


@pytest.mark.asyncio
async def test_translate_rejects_empty_and_oversized_text(client):
    headers = await _register(client, "translator2")
    r = await client.post("/api/translate", json={"text": ""}, headers=headers)
    assert r.status_code == 422
    r = await client.post(
        "/api/translate", json={"text": "x" * 1001}, headers=headers
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_translate_degrades_cleanly_when_the_llm_fails(client):
    from app.services.llm_adapter import LLMError

    headers = await _register(client, "translator3")
    with patch(
        "app.routers.translation.llm_adapter.chat",
        new=AsyncMock(side_effect=LLMError("down")),
    ):
        r = await client.post(
            "/api/translate", json={"text": "hola"}, headers=headers
        )
    assert r.status_code == 502
    assert r.json()["detail"] == "translation_failed"
