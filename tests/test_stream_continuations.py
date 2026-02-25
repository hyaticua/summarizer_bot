"""
Unit tests for _stream_with_search() continuation and tool-round logic.

Tests cover:
- Normal end_turn with text
- Server-side continuations (pause_turn) within limits
- Client-side tool rounds (tool_use) within limits
- Max continuations exceeded → wrap-up turn
- Max tool rounds exceeded → wrap-up turn with tool errors
- Empty text after loop triggers final safety-net turn
- Final safety-net turn after max continuations wrap-up still empty
- Mixed continuations and tool rounds
- File IDs accumulated across all iterations
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch

from summarizer_bot.summarizer import AnthropicClient, LLMResponse, MAX_CONTINUATIONS, MAX_TOOL_ROUNDS


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def make_usage(**overrides):
    """Create a mock usage object."""
    u = Mock()
    u.input_tokens = overrides.get("input_tokens", 100)
    u.output_tokens = overrides.get("output_tokens", 50)
    u.cache_read_input_tokens = 0
    u.cache_creation_input_tokens = 0
    return u


def make_text_block(text):
    b = Mock()
    b.type = "text"
    b.text = text
    return b


def make_tool_use_block(name="get_server_members", tool_id="tool_1", input_data=None):
    b = Mock()
    b.type = "tool_use"
    b.name = name
    b.id = tool_id
    b.input = input_data or {}
    return b


def make_server_tool_block():
    """A server-side tool result block (e.g. web search result) — not text."""
    b = Mock()
    b.type = "server_tool_use"
    b.name = "web_search"
    return b


def make_code_exec_result_block(file_id=None, stdout=""):
    b = Mock()
    b.type = "code_execution_tool_result"
    content = {"stdout": stdout, "content": []}
    if file_id:
        content["content"].append({"file_id": file_id})
    b.content = content
    return b


def make_response(stop_reason, content_blocks, usage=None):
    r = Mock()
    r.stop_reason = stop_reason
    r.content = content_blocks
    r.usage = usage or make_usage()
    return r


def make_tool_executor():
    ex = AsyncMock()
    ex.execute = AsyncMock(return_value="tool result text")
    ex.get_available_tools = Mock(return_value=[])
    return ex


@pytest.fixture
def client():
    """Create an AnthropicClient with a dummy key (API never called directly)."""
    with patch("summarizer_bot.summarizer.AsyncAnthropic"):
        c = AnthropicClient(key="test-key")
    return c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEndTurnWithText:
    """Model responds immediately with text — simplest case."""

    @pytest.mark.asyncio
    async def test_returns_text(self, client):
        resp = make_response("end_turn", [make_text_block("Hello!")])
        client._stream_single_request = AsyncMock(return_value=resp)

        result = await client._stream_with_search(
            [{"role": "user", "content": "hi"}], "sys"
        )

        assert result.text == "Hello!"
        assert client._stream_single_request.await_count == 1

    @pytest.mark.asyncio
    async def test_no_final_turn_when_text_present(self, client):
        """The safety-net turn should NOT fire when text is already present."""
        resp = make_response("end_turn", [make_text_block("Got it")])
        client._stream_single_request = AsyncMock(return_value=resp)

        result = await client._stream_with_search(
            [{"role": "user", "content": "hi"}], "sys"
        )

        assert result.text == "Got it"
        assert client._stream_single_request.await_count == 1


class TestServerContinuations:
    """pause_turn handling — server-side tools like web search."""

    @pytest.mark.asyncio
    async def test_single_continuation(self, client):
        search_resp = make_response("pause_turn", [make_server_tool_block()])
        final_resp = make_response("end_turn", [make_text_block("Search result")])
        client._stream_single_request = AsyncMock(side_effect=[search_resp, final_resp])

        result = await client._stream_with_search(
            [{"role": "user", "content": "search something"}], "sys"
        )

        assert result.text == "Search result"
        assert client._stream_single_request.await_count == 2

    @pytest.mark.asyncio
    async def test_max_continuations_triggers_wrap_up(self, client):
        """After MAX_CONTINUATIONS pause_turns, model gets a wrap-up prompt."""
        search_responses = [
            make_response("pause_turn", [make_server_tool_block()])
            for _ in range(MAX_CONTINUATIONS + 1)
        ]
        wrap_up_resp = make_response("end_turn", [make_text_block("Wrapped up")])
        client._stream_single_request = AsyncMock(
            side_effect=search_responses + [wrap_up_resp]
        )

        result = await client._stream_with_search(
            [{"role": "user", "content": "search"}], "sys"
        )

        assert result.text == "Wrapped up"
        # MAX_CONTINUATIONS+1 normal iterations + 1 wrap-up
        assert client._stream_single_request.await_count == MAX_CONTINUATIONS + 2


class TestClientToolRounds:
    """tool_use handling — client-side Discord tools."""

    @pytest.mark.asyncio
    async def test_single_tool_round(self, client):
        tool_resp = make_response("tool_use", [
            make_text_block("Let me check..."),
            make_tool_use_block("get_server_members", "t1"),
        ])
        final_resp = make_response("end_turn", [make_text_block("Here are the members")])
        client._stream_single_request = AsyncMock(side_effect=[tool_resp, final_resp])
        executor = make_tool_executor()

        result = await client._stream_with_search(
            [{"role": "user", "content": "who is online"}], "sys",
            tool_executor=executor,
        )

        assert result.text == "Here are the members"
        executor.execute.assert_awaited_once_with("get_server_members", {})

    @pytest.mark.asyncio
    async def test_max_tool_rounds_triggers_wrap_up(self, client):
        """After MAX_TOOL_ROUNDS tool_use responses, model gets error results + wrap-up."""
        tool_responses = [
            make_response("tool_use", [
                make_tool_use_block("get_server_members", f"t{i}"),
            ])
            for i in range(MAX_TOOL_ROUNDS + 1)
        ]
        wrap_up_resp = make_response("end_turn", [make_text_block("Done")])
        client._stream_single_request = AsyncMock(
            side_effect=tool_responses + [wrap_up_resp]
        )
        executor = make_tool_executor()

        result = await client._stream_with_search(
            [{"role": "user", "content": "check stuff"}], "sys",
            tool_executor=executor,
        )

        assert result.text == "Done"
        # Tools executed for MAX_TOOL_ROUNDS rounds, then the exceeded round is NOT executed
        assert executor.execute.await_count == MAX_TOOL_ROUNDS

    @pytest.mark.asyncio
    async def test_tool_use_ignored_without_executor(self, client):
        """tool_use stop reason without an executor falls through to else/break,
        then safety-net fires since text is empty."""
        resp = make_response("tool_use", [make_tool_use_block()])
        safety_resp = make_response("end_turn", [make_text_block("Recovered")])
        client._stream_single_request = AsyncMock(side_effect=[resp, safety_resp])

        result = await client._stream_with_search(
            [{"role": "user", "content": "hi"}], "sys",
            tool_executor=None,
        )

        # Falls through to else branch with no text, safety-net fires
        assert result.text == "Recovered"
        assert client._stream_single_request.await_count == 2


class TestFinalSafetyNet:
    """When text is empty after the main loop, one more turn is attempted."""

    @pytest.mark.asyncio
    async def test_empty_end_turn_triggers_final_turn(self, client):
        """end_turn with no text blocks → safety-net fires."""
        empty_resp = make_response("end_turn", [make_server_tool_block()])
        safety_resp = make_response("end_turn", [make_text_block("Recovery!")])
        client._stream_single_request = AsyncMock(side_effect=[empty_resp, safety_resp])

        result = await client._stream_with_search(
            [{"role": "user", "content": "hi"}], "sys"
        )

        assert result.text == "Recovery!"
        assert client._stream_single_request.await_count == 2

    @pytest.mark.asyncio
    async def test_empty_after_continuations_wrap_up(self, client):
        """Wrap-up from max continuations also produces no text → safety-net fires."""
        search_responses = [
            make_response("pause_turn", [make_server_tool_block()])
            for _ in range(MAX_CONTINUATIONS + 1)
        ]
        # Wrap-up call also returns no text
        empty_wrap = make_response("end_turn", [make_server_tool_block()])
        # Safety-net recovers
        safety_resp = make_response("end_turn", [make_text_block("Recovered")])
        client._stream_single_request = AsyncMock(
            side_effect=search_responses + [empty_wrap, safety_resp]
        )

        result = await client._stream_with_search(
            [{"role": "user", "content": "search"}], "sys"
        )

        assert result.text == "Recovered"

    @pytest.mark.asyncio
    async def test_safety_net_also_fails_returns_empty(self, client):
        """If even the safety-net turn produces no text, we get an empty response."""
        empty_resp = make_response("end_turn", [make_server_tool_block()])
        still_empty = make_response("end_turn", [make_server_tool_block()])
        client._stream_single_request = AsyncMock(side_effect=[empty_resp, still_empty])

        result = await client._stream_with_search(
            [{"role": "user", "content": "hi"}], "sys"
        )

        assert result.text == ""
        assert client._stream_single_request.await_count == 2


class TestMixedContinuationsAndTools:
    """Interleaved pause_turn and tool_use stop reasons."""

    @pytest.mark.asyncio
    async def test_continuation_then_tool_then_end(self, client):
        search_resp = make_response("pause_turn", [make_server_tool_block()])
        tool_resp = make_response("tool_use", [
            make_tool_use_block("list_channels", "t1"),
        ])
        final_resp = make_response("end_turn", [make_text_block("All done")])
        client._stream_single_request = AsyncMock(
            side_effect=[search_resp, tool_resp, final_resp]
        )
        executor = make_tool_executor()

        result = await client._stream_with_search(
            [{"role": "user", "content": "do stuff"}], "sys",
            tool_executor=executor,
        )

        assert result.text == "All done"
        assert client._stream_single_request.await_count == 3
        executor.execute.assert_awaited_once()


class TestFileIdAccumulation:
    """File IDs from code execution are collected across all iterations."""

    @pytest.mark.asyncio
    async def test_file_ids_from_multiple_iterations(self, client):
        resp1 = make_response("pause_turn", [
            make_code_exec_result_block(file_id="file_1", stdout="output1"),
        ])
        resp2 = make_response("end_turn", [
            make_text_block("Here's your chart"),
            make_code_exec_result_block(file_id="file_2", stdout="output2"),
        ])
        client._stream_single_request = AsyncMock(side_effect=[resp1, resp2])
        client._download_files = AsyncMock(return_value=[])

        result = await client._stream_with_search(
            [{"role": "user", "content": "make a chart"}], "sys"
        )

        # Both file IDs collected
        client._download_files.assert_awaited_once()
        downloaded_ids = client._download_files.call_args[0][0]
        assert "file_1" in downloaded_ids
        assert "file_2" in downloaded_ids

    @pytest.mark.asyncio
    async def test_file_ids_from_safety_net_turn(self, client):
        """Files produced in the safety-net turn are also collected."""
        empty_resp = make_response("end_turn", [make_server_tool_block()])
        safety_resp = make_response("end_turn", [
            make_text_block("Here's the file"),
            make_code_exec_result_block(file_id="safety_file"),
        ])
        client._stream_single_request = AsyncMock(side_effect=[empty_resp, safety_resp])
        client._download_files = AsyncMock(return_value=[])

        await client._stream_with_search(
            [{"role": "user", "content": "generate"}], "sys"
        )

        downloaded_ids = client._download_files.call_args[0][0]
        assert "safety_file" in downloaded_ids


class TestStatusCallbacks:
    """Status callback is passed through to all iterations including safety-net."""

    @pytest.mark.asyncio
    async def test_status_callback_passed_to_all_calls(self, client):
        resp1 = make_response("pause_turn", [make_server_tool_block()])
        resp2 = make_response("end_turn", [make_text_block("done")])
        client._stream_single_request = AsyncMock(side_effect=[resp1, resp2])
        callback = AsyncMock()

        await client._stream_with_search(
            [{"role": "user", "content": "hi"}], "sys",
            status_callback=callback,
        )

        # Both calls receive the callback
        for call in client._stream_single_request.call_args_list:
            assert call[0][2] is callback  # 3rd positional arg = status_callback
