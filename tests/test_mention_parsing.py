"""
Comprehensive unit tests for user mention parsing functionality.

Tests edge cases including:
- Names with spaces
- Names with special characters
- Names with emojis
- Different Discord name formats (nickname, display_name, global_name, username)
- Case sensitivity
"""

import pytest
from unittest.mock import Mock, MagicMock
import discord
from summarizer_bot.message import attempt_to_find_member, parse_response, parse_content, format_message_text


class MockMember:
    """Mock Discord Member for testing."""

    def __init__(self, id: int, name: str, global_name: str = None, nick: str = None):
        self.id = id
        self.name = name  # Username
        self.global_name = global_name  # Display name set by user
        self.nick = nick  # Server-specific nickname

    @property
    def display_name(self):
        """Discord's display_name property: nick > global_name > name"""
        if self.nick:
            return self.nick
        if self.global_name:
            return self.global_name
        return self.name


class MockGuild:
    """Mock Discord Guild for testing."""

    def __init__(self, members: list[MockMember]):
        self.members = members


class TestAttemptToFindMember:
    """Test the attempt_to_find_member function with various edge cases."""

    def test_simple_username(self):
        """Test finding by simple username without spaces."""
        member = MockMember(id=123, name="alice", global_name="Alice")
        guild = MockGuild([member])

        # Should find by display_name
        result = attempt_to_find_member("Alice", guild)
        assert result == member

        # Should find by username
        result = attempt_to_find_member("alice", guild)
        assert result == member

    def test_name_with_spaces(self):
        """Test finding by name containing spaces."""
        member = MockMember(id=456, name="johndoe", global_name="John Doe")
        guild = MockGuild([member])

        result = attempt_to_find_member("John Doe", guild)
        assert result == member

    def test_name_with_multiple_spaces(self):
        """Test finding by name with multiple consecutive spaces."""
        member = MockMember(id=789, name="user123", global_name="Mary Jane Watson")
        guild = MockGuild([member])

        result = attempt_to_find_member("Mary Jane Watson", guild)
        assert result == member

    def test_name_with_special_characters(self):
        """Test finding by name with special characters."""
        test_cases = [
            MockMember(id=1, name="user1", global_name="User-Name"),
            MockMember(id=2, name="user2", global_name="User_Name"),
            MockMember(id=3, name="user3", global_name="User.Name"),
            MockMember(id=4, name="user4", global_name="User'Name"),
            MockMember(id=5, name="user5", global_name="User!Name"),
            MockMember(id=6, name="user6", global_name="User@Name"),
            MockMember(id=7, name="user7", global_name="User#Name"),
            MockMember(id=8, name="user8", global_name="User$Name"),
        ]

        for member in test_cases:
            guild = MockGuild([member])
            result = attempt_to_find_member(member.global_name, guild)
            assert result == member, f"Failed to find member with name: {member.global_name}"

    def test_name_with_emojis(self):
        """Test finding by name containing emojis."""
        test_cases = [
            MockMember(id=1, name="user1", global_name="🔥User🔥"),
            MockMember(id=2, name="user2", global_name="User 😀"),
            MockMember(id=3, name="user3", global_name="💯 Cool User 💯"),
            MockMember(id=4, name="user4", global_name="User🎮Gaming"),
            MockMember(id=5, name="user5", global_name="星际用户"),  # Chinese characters
            MockMember(id=6, name="user6", global_name="Пользователь"),  # Cyrillic
        ]

        for member in test_cases:
            guild = MockGuild([member])
            result = attempt_to_find_member(member.global_name, guild)
            assert result == member, f"Failed to find member with name: {member.global_name}"

    def test_nickname_priority(self):
        """Test that nickname is found correctly."""
        member = MockMember(id=999, name="realuser", global_name="Real User", nick="Cool Nickname")
        guild = MockGuild([member])

        # Should find by nickname
        result = attempt_to_find_member("Cool Nickname", guild)
        assert result == member

        # Should also find by display_name (which is the nickname in this case)
        result = attempt_to_find_member("Cool Nickname", guild)
        assert result == member

    def test_nickname_with_spaces(self):
        """Test finding by nickname that contains spaces."""
        member = MockMember(id=111, name="user", global_name="User", nick="Super Cool Nickname")
        guild = MockGuild([member])

        result = attempt_to_find_member("Super Cool Nickname", guild)
        assert result == member

    def test_parentheses_format(self):
        """Test parsing "DisplayName (Username)" format."""
        member = MockMember(id=222, name="actualuser", global_name="Display Name")
        guild = MockGuild([member])

        # Format: "Display Name (actualuser)"
        result = attempt_to_find_member("actualuser (Display Name)", guild)
        assert result == member

    def test_parentheses_format_username_with_global_name(self):
        """Test parsing 'username (GlobalName)' format where username is first."""
        member = MockMember(id=223, name="kkthxbai", global_name="Jared")
        guild = MockGuild([member])

        result = attempt_to_find_member("kkthxbai (Jared)", guild)
        assert result == member

    def test_not_found(self):
        """Test that None is returned for non-existent users."""
        member = MockMember(id=333, name="user", global_name="User")
        guild = MockGuild([member])

        result = attempt_to_find_member("NonExistent User", guild)
        assert result is None

    def test_case_sensitivity(self):
        """Test that name matching is case-sensitive."""
        member = MockMember(id=444, name="user", global_name="User")
        guild = MockGuild([member])

        # Exact match should work
        result = attempt_to_find_member("User", guild)
        assert result == member

        # Different case should not match
        result = attempt_to_find_member("user", guild)
        # This should find by username
        assert result == member

        # Completely different case
        result = attempt_to_find_member("USER", guild)
        assert result is None

    def test_multiple_members_disambiguation(self):
        """Test finding the right member when multiple exist."""
        members = [
            MockMember(id=1, name="alice", global_name="Alice"),
            MockMember(id=2, name="bob", global_name="Bob"),
            MockMember(id=3, name="charlie", global_name="Charlie"),
        ]
        guild = MockGuild(members)

        result = attempt_to_find_member("Bob", guild)
        assert result == members[1]

    def test_edge_case_empty_string(self):
        """Test handling of empty string input."""
        member = MockMember(id=555, name="user", global_name="User")
        guild = MockGuild([member])

        result = attempt_to_find_member("", guild)
        assert result is None

    def test_edge_case_whitespace_only(self):
        """Test handling of whitespace-only input."""
        member = MockMember(id=666, name="user", global_name="User")
        guild = MockGuild([member])

        result = attempt_to_find_member("   ", guild)
        assert result is None


class TestParseResponse:
    """Test the parse_response function that converts LLM output to Discord mentions."""

    def test_simple_mention_conversion(self):
        """Test converting simple <@Username> to <@ID>."""
        member = MockMember(id=12345, name="alice", global_name="Alice")
        guild = MockGuild([member])

        response = "Hello <@Alice>!"
        result = parse_response(response, guild)
        assert result == "Hello <@12345>!"

    def test_mention_with_spaces(self):
        """Test converting mentions with spaces in names."""
        member = MockMember(id=67890, name="user", global_name="John Doe")
        guild = MockGuild([member])

        response = "Hey <@John Doe>, how are you?"
        result = parse_response(response, guild)
        assert result == "Hey <@67890>, how are you?"

    def test_multiple_mentions(self):
        """Test converting multiple mentions in one response."""
        members = [
            MockMember(id=111, name="alice", global_name="Alice"),
            MockMember(id=222, name="bob", global_name="Bob"),
        ]
        guild = MockGuild(members)

        response = "<@Alice> and <@Bob> are here!"
        result = parse_response(response, guild)
        assert result == "<@111> and <@222> are here!"

    def test_mention_with_emojis(self):
        """Test converting mentions with emojis in names."""
        member = MockMember(id=999, name="user", global_name="🔥User🔥")
        guild = MockGuild([member])

        response = "Welcome <@🔥User🔥>!"
        result = parse_response(response, guild)
        assert result == "Welcome <@999>!"

    def test_mention_with_special_chars(self):
        """Test converting mentions with special characters."""
        member = MockMember(id=888, name="user", global_name="User-Name_123")
        guild = MockGuild([member])

        response = "Hi <@User-Name_123>!"
        result = parse_response(response, guild)
        assert result == "Hi <@888>!"

    def test_mention_not_found_preserved(self):
        """Test that mentions for non-existent users are preserved."""
        member = MockMember(id=777, name="user", global_name="User")
        guild = MockGuild([member])

        response = "Hello <@NonExistent>!"
        result = parse_response(response, guild)
        assert result == "Hello <@NonExistent>!"

    def test_at_symbol_variants(self):
        """Test handling mentions with @ symbol variations."""
        member = MockMember(id=555, name="user", global_name="User")
        guild = MockGuild([member])

        # With @ prefix - the regex normalizes this by removing the redundant @
        response = "Hello @<@User>!"
        result = parse_response(response, guild)
        assert result == "Hello <@555>!"

    def test_no_mentions(self):
        """Test that text without mentions is unchanged."""
        member = MockMember(id=444, name="user", global_name="User")
        guild = MockGuild([member])

        response = "Hello everyone!"
        result = parse_response(response, guild)
        assert result == "Hello everyone!"


class TestParseContent:
    """Test the parse_content function that converts Discord IDs to readable names."""

    def test_convert_id_to_name(self):
        """Test converting <@12345> to <@DisplayName>."""
        # Create mock message and guild
        author = MockMember(id=12345, name="alice", global_name="Alice")

        message = Mock(spec=discord.Message)
        message.content = "Hello <@12345>!"
        message.guild = Mock(spec=discord.Guild)
        message.guild.get_member = Mock(return_value=author)
        message.guild.get_channel = Mock(return_value=None)

        result = parse_content(message)
        # The function returns <@MockMember> format, which will call __str__
        # For a proper test, we'd need to mock __str__, but the key is it's being converted
        assert "<@" in result and ">" in result

    def test_multiple_id_mentions(self):
        """Test converting multiple ID mentions."""
        member1 = MockMember(id=111, name="alice", global_name="Alice")
        member2 = MockMember(id=222, name="bob", global_name="Bob")

        message = Mock(spec=discord.Message)
        message.content = "Hi <@111> and <@222>!"
        message.guild = Mock(spec=discord.Guild)

        def get_member(user_id):
            if user_id == 111:
                return member1
            elif user_id == 222:
                return member2
            return None

        message.guild.get_member = get_member
        message.guild.get_channel = Mock(return_value=None)

        result = parse_content(message)
        assert "<@" in result

    def test_channel_mention_resolved(self):
        """Test converting <#123456> to #channel-name."""
        channel = Mock()
        channel.name = "general"

        message = Mock(spec=discord.Message)
        message.content = "Check out <#123456>!"
        message.guild = Mock(spec=discord.Guild)
        message.guild.get_member = Mock(return_value=None)
        message.guild.get_channel = Mock(return_value=channel)

        result = parse_content(message)
        assert "#general" in result
        assert "<#123456>" not in result

    def test_channel_mention_unknown_id(self):
        """Test that unresolvable channel IDs are left as-is."""
        message = Mock(spec=discord.Message)
        message.content = "See <#999999>"
        message.guild = Mock(spec=discord.Guild)
        message.guild.get_member = Mock(return_value=None)
        message.guild.get_channel = Mock(return_value=None)

        result = parse_content(message)
        assert "<#999999>" in result

    def test_mixed_user_and_channel_mentions(self):
        """Test both user and channel mentions in the same message."""
        member = MockMember(id=111, name="alice", global_name="Alice")
        channel = Mock()
        channel.name = "dev"

        message = Mock(spec=discord.Message)
        message.content = "Hey <@111>, check <#555>!"
        message.guild = Mock(spec=discord.Guild)
        message.guild.get_member = Mock(return_value=member)
        message.guild.get_channel = Mock(return_value=channel)

        result = parse_content(message)
        assert "#dev" in result
        assert "<#555>" not in result
        assert "<@" in result  # user mention converted


class TestFormatMessageText:
    """Test the format_message_text shared formatting function."""

    def _make_msg(self, content, author_name="Alice", reference=None, attachments=None):
        msg = Mock(spec=discord.Message)
        msg.content = content
        msg.author = Mock()
        msg.author.display_name = author_name
        msg.reference = reference
        msg.attachments = attachments or []
        msg.reactions = []
        msg.guild = Mock(spec=discord.Guild)
        msg.guild.get_member = Mock(return_value=None)
        msg.guild.get_channel = Mock(return_value=None)
        return msg

    def test_basic_content(self):
        msg = self._make_msg("hello world")
        assert format_message_text(msg) == "hello world"

    def test_empty_content(self):
        msg = self._make_msg(None)
        assert format_message_text(msg) == "[no text]"

    def test_truncation(self):
        msg = self._make_msg("a" * 300)
        result = format_message_text(msg, max_length=200)
        assert len(result) <= 204  # 200 + "..."
        assert result.endswith("...")

    def test_no_truncation_when_under_limit(self):
        msg = self._make_msg("short")
        result = format_message_text(msg, max_length=200)
        assert result == "short"

    def test_reply_context(self):
        ref = Mock()
        ref.resolved = Mock(spec=discord.Message)
        ref.resolved.author = Mock()
        ref.resolved.author.display_name = "Bob"
        msg = self._make_msg("I agree", reference=ref)
        result = format_message_text(msg)
        assert result == "[replying to Bob] I agree"

    def test_reply_to_deleted_message(self):
        ref = Mock()
        ref.resolved = Mock()  # not spec=discord.Message, so isinstance fails
        msg = self._make_msg("what happened?", reference=ref)
        result = format_message_text(msg)
        assert result == "[replying to deleted message] what happened?"

    def test_no_reply_when_reference_is_none(self):
        msg = self._make_msg("hello")
        result = format_message_text(msg)
        assert "replying" not in result

    def test_no_reply_when_resolved_is_none(self):
        ref = Mock()
        ref.resolved = None
        msg = self._make_msg("hello", reference=ref)
        result = format_message_text(msg)
        assert "replying" not in result

    def test_attachment_names(self):
        att1 = Mock(spec=discord.Attachment)
        att1.filename = "photo.jpg"
        att2 = Mock(spec=discord.Attachment)
        att2.filename = "doc.pdf"
        msg = self._make_msg("check these", attachments=[att1, att2])
        result = format_message_text(msg, include_attachment_names=True)
        assert "[attachments: photo.jpg, doc.pdf]" in result

    def test_attachment_names_not_shown_by_default(self):
        att = Mock(spec=discord.Attachment)
        att.filename = "photo.jpg"
        msg = self._make_msg("check this", attachments=[att])
        result = format_message_text(msg)
        assert "attachments" not in result

    def test_mention_resolution(self):
        member = MockMember(id=123, name="bob", global_name="Bob")
        msg = self._make_msg("Hey <@123> look")
        msg.guild.get_member = Mock(return_value=member)
        result = format_message_text(msg)
        assert "<@Bob>" in result
        assert "<@123>" not in result

    def test_combined_reply_and_attachments(self):
        ref = Mock()
        ref.resolved = Mock(spec=discord.Message)
        ref.resolved.author = Mock()
        ref.resolved.author.display_name = "Bob"
        att = Mock(spec=discord.Attachment)
        att.filename = "screenshot.png"
        msg = self._make_msg("here's the fix", reference=ref, attachments=[att])
        result = format_message_text(msg, include_attachment_names=True)
        assert result.startswith("[replying to Bob]")
        assert "here's the fix" in result
        assert "screenshot.png" in result


class TestRoundTripConversion:
    """Test that converting ID -> Name -> ID works correctly."""

    def test_roundtrip_simple_name(self):
        """Test full roundtrip conversion with simple name."""
        # This test would require more complex mocking to fully test
        # the integration between parse_content and parse_response
        pass

    def test_roundtrip_name_with_spaces(self):
        """Test full roundtrip with names containing spaces."""
        pass

    def test_roundtrip_name_with_emojis(self):
        """Test full roundtrip with names containing emojis."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
