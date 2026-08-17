"""Fork: learners set their own usage limits (0 = unlimited)."""

from __future__ import annotations

from unittest.mock import patch

import pytest


async def _register(client, username="limits"):
    r = await client.post(
        "/api/auth/register",
        json={
            "username": username,
            "display_name": "L",
            "email": f"{username}@example.com",
            "password": "Passw0rd!123",
            "native_language": "en",
            "accept_terms": True,
        },
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_user_can_set_and_unset_their_own_limits(client):
    headers = await _register(client)
    r = await client.patch(
        "/api/auth/me",
        json={"conversation_daily_minutes": 60, "monthly_tokens_limit": 0},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    q = await client.get("/api/auth/quota", headers=headers)
    data = q.json()
    assert data["minutes_limit"] == 60
    assert data["tokens_unlimited"] is True


@pytest.mark.asyncio
async def test_paywall_mode_blocks_non_admins(client):
    headers = await _register(client, "limits2")
    # user 2 is not admin (first registered user in the other test may be)
    with patch("app.routers.auth.settings.STRIPE_ENABLED", True):
        r = await client.patch(
            "/api/auth/me",
            json={"conversation_daily_minutes": 999},
            headers=headers,
        )
    assert r.status_code in (200, 403)  # admins pass, everyone else 403
    if r.status_code == 200:
        # first-user-is-admin made this account admin; verify the gate for
        # a second, non-admin account instead
        headers2 = await _register(client, "limits3")
        with patch("app.routers.auth.settings.STRIPE_ENABLED", True):
            r2 = await client.patch(
                "/api/auth/me",
                json={"conversation_daily_minutes": 999},
                headers=headers2,
            )
        assert r2.status_code == 403


@pytest.mark.asyncio
async def test_negative_limits_are_rejected(client):
    headers = await _register(client, "limits4")
    r = await client.patch(
        "/api/auth/me",
        json={"conversation_daily_minutes": -5},
        headers=headers,
    )
    assert r.status_code == 422
