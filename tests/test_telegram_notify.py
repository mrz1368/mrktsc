"""Offline Telegram send hardening — failures must not raise."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from telegram_notify import send_html_message


def test_send_html_message_network_error_returns_false(capsys) -> None:
    with patch(
        "telegram_notify.requests.post",
        side_effect=requests.ConnectionError("network down"),
    ):
        ok = send_html_message("token", "chat", "<b>hi</b>", context="setup RY.TO")

    assert ok is False
    captured = capsys.readouterr()
    assert "[TELEGRAM FAIL] setup RY.TO" in captured.out
    assert "network down" in captured.out


def test_send_html_message_api_error_returns_false(capsys) -> None:
    response = MagicMock()
    response.status_code = 400
    response.text = "bad request"
    response.json.return_value = {"ok": False, "description": "chat not found"}

    with patch("telegram_notify.requests.post", return_value=response):
        ok = send_html_message("token", "chat", "<b>hi</b>", context="idle cash")

    assert ok is False
    captured = capsys.readouterr()
    assert "[TELEGRAM FAIL] idle cash" in captured.out
    assert "chat not found" in captured.out


def test_send_html_message_success_returns_true() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"ok": True, "result": {"message_id": 1}}

    with patch("telegram_notify.requests.post", return_value=response) as post:
        ok = send_html_message("token", "chat", "<b>hi</b>", context="watch XIU.TO")

    assert ok is True
    post.assert_called_once()
