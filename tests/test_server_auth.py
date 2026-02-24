"""
Unit tests for server authorization (config.py auth methods).

Tests cover:
- is_server_authorized(): backward compat (key absent) and active check
- polite_declined tracking: add, clear, dedup
- get/set unauthorized_mode
"""

import pytest
from collections import defaultdict

from summarizer_bot.config import Config


@pytest.fixture
def empty_config():
    """Config with no authorization keys set."""
    return Config(defaultdict(dict))


@pytest.fixture
def active_config():
    """Config with authorization active and two servers."""
    data = defaultdict(dict, {
        "authorized_servers": [111, 222],
        "unauthorized_mode": "polite",
        "polite_declined": [333],
    })
    return Config(data)


# --- is_server_authorized ---

class TestIsServerAuthorized:
    def test_absent_key_allows_all(self, empty_config):
        """When authorized_servers key is absent, all servers are allowed."""
        assert empty_config.is_server_authorized(999) is True
        assert empty_config.is_server_authorized(0) is True

    def test_active_allows_listed(self, active_config):
        assert active_config.is_server_authorized(111) is True
        assert active_config.is_server_authorized(222) is True

    def test_active_blocks_unlisted(self, active_config):
        assert active_config.is_server_authorized(999) is False

    def test_empty_list_blocks_all(self):
        cfg = Config(defaultdict(dict, {"authorized_servers": []}))
        assert cfg.is_server_authorized(111) is False


# --- get_authorized_servers ---

class TestGetAuthorizedServers:
    def test_returns_none_when_absent(self, empty_config):
        assert empty_config.get_authorized_servers() is None

    def test_returns_list_when_present(self, active_config):
        assert active_config.get_authorized_servers() == [111, 222]


# --- unauthorized_mode ---

class TestUnauthorizedMode:
    def test_default_is_ignore(self, empty_config):
        assert empty_config.get_unauthorized_mode() == "ignore"

    def test_returns_configured_mode(self, active_config):
        assert active_config.get_unauthorized_mode() == "polite"


# --- polite_declined ---

class TestPoliteDeclined:
    def test_default_empty(self, empty_config):
        assert empty_config.get_polite_declined() == []

    def test_returns_existing(self, active_config):
        assert active_config.get_polite_declined() == [333]

    @pytest.mark.asyncio
    async def test_add_polite_declined(self, empty_config, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.json").write_text("{}")
        await empty_config.add_polite_declined(444)
        assert 444 in empty_config.get_polite_declined()

    @pytest.mark.asyncio
    async def test_add_polite_declined_dedup(self, active_config, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.json").write_text("{}")
        await active_config.add_polite_declined(333)  # already present
        assert active_config.get_polite_declined().count(333) == 1

    @pytest.mark.asyncio
    async def test_clear_polite_declined(self, active_config, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.json").write_text("{}")
        await active_config.clear_polite_declined()
        assert active_config.get_polite_declined() == []
