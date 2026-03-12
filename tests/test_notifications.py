"""Test email notifications."""

from unittest.mock import AsyncMock, patch

import pytest

from arclane.notifications import (
    send_credits_low_email,
    send_cycle_complete_email,
    send_password_reset_email,
    send_welcome_email,
)


@pytest.mark.asyncio
@patch("arclane.provisioning.email.send_email", new_callable=AsyncMock)
async def test_welcome_email_calls_send_email(mock_send):
    await send_welcome_email("My Biz", "owner@test.com", "my-biz")

    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert args[0] == "arclane"  # from_slug
    assert args[1] == "owner@test.com"  # to
    assert "Welcome" in args[2]  # subject
    assert "My Biz" in args[2]


@pytest.mark.asyncio
@patch("arclane.provisioning.email.send_email", new_callable=AsyncMock)
async def test_welcome_email_does_not_raise_on_error(mock_send):
    mock_send.side_effect = RuntimeError("Resend is down")
    await send_welcome_email("My Biz", "owner@test.com", "my-biz")


@pytest.mark.asyncio
@patch("arclane.provisioning.email.send_email", new_callable=AsyncMock)
async def test_cycle_complete_email_calls_send_email(mock_send):
    await send_cycle_complete_email("My Biz", "owner@test.com", "my-biz", 8, 10)

    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert args[0] == "arclane"
    assert args[1] == "owner@test.com"
    assert "8" in args[2] and "10" in args[2]
    assert "Cycle" in args[2]


@pytest.mark.asyncio
@patch("arclane.provisioning.email.send_email", new_callable=AsyncMock)
async def test_cycle_complete_email_does_not_raise_on_error(mock_send):
    mock_send.side_effect = RuntimeError("Resend is down")
    await send_cycle_complete_email("My Biz", "owner@test.com", "my-biz", 5, 5)


@pytest.mark.asyncio
@patch("arclane.provisioning.email.send_email", new_callable=AsyncMock)
async def test_credits_low_email_calls_send_email(mock_send):
    await send_credits_low_email("My Biz", "owner@test.com", 2)

    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert args[0] == "arclane"
    assert args[1] == "owner@test.com"
    assert "2" in args[2]


@pytest.mark.asyncio
@patch("arclane.provisioning.email.send_email", new_callable=AsyncMock)
async def test_credits_low_email_does_not_raise_on_error(mock_send):
    mock_send.side_effect = RuntimeError("Resend is down")
    await send_credits_low_email("My Biz", "owner@test.com", 1)


@pytest.mark.asyncio
@patch("arclane.provisioning.email.send_email", new_callable=AsyncMock)
async def test_password_reset_email_calls_send_email(mock_send):
    await send_password_reset_email("user@test.com", "abc123token")

    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert args[0] == "arclane"
    assert args[1] == "user@test.com"
    assert "reset" in args[2].lower()
    assert "abc123token" in args[3]  # token in body


@pytest.mark.asyncio
@patch("arclane.provisioning.email.send_email", new_callable=AsyncMock)
async def test_password_reset_email_does_not_raise_on_error(mock_send):
    mock_send.side_effect = RuntimeError("Resend is down")
    await send_password_reset_email("user@test.com", "abc123token")
