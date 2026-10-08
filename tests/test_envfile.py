import pytest

from pipeline.envfile import MissingKeyError, get_key, key_status

FAKE_SECRET = "test-value-not-a-real-key-123"


def write_env(tmp_path, text, encoding="utf-8"):
    path = tmp_path / ".env"
    path.write_text(text, encoding=encoding)
    return path


def test_reads_a_set_key(tmp_path):
    env = write_env(tmp_path, f"# comment\nANTHROPIC_API_KEY={FAKE_SECRET}\n")
    assert get_key("ANTHROPIC_API_KEY", env) == FAKE_SECRET
    assert key_status("ANTHROPIC_API_KEY", env) == "set"


def test_tolerates_quotes_spaces_and_notepad_byte_order_mark(tmp_path):
    env = write_env(tmp_path, f'ANTHROPIC_API_KEY = "{FAKE_SECRET}"\n', encoding="utf-8-sig")
    assert get_key("ANTHROPIC_API_KEY", env) == FAKE_SECRET


def test_placeholder_names_the_line_to_fill_in(tmp_path):
    env = write_env(tmp_path, "# keys\n\nANTHROPIC_API_KEY=paste-your-key-here\n")
    assert key_status("ANTHROPIC_API_KEY", env) == "placeholder"
    with pytest.raises(MissingKeyError, match="line 3 of .env"):
        get_key("ANTHROPIC_API_KEY", env)


def test_empty_value_counts_as_placeholder(tmp_path):
    env = write_env(tmp_path, "ANTHROPIC_API_KEY=\n")
    assert key_status("ANTHROPIC_API_KEY", env) == "placeholder"


def test_missing_line_and_missing_file(tmp_path):
    env = write_env(tmp_path, "OTHER=1\n")
    assert key_status("ANTHROPIC_API_KEY", env) == "missing"
    with pytest.raises(MissingKeyError, match="no ANTHROPIC_API_KEY line"):
        get_key("ANTHROPIC_API_KEY", env)
    assert key_status("ANTHROPIC_API_KEY", tmp_path / "absent") == "no_env_file"
    with pytest.raises(MissingKeyError, match="no .env file"):
        get_key("ANTHROPIC_API_KEY", tmp_path / "absent")


def test_error_messages_never_contain_other_values(tmp_path):
    env = write_env(tmp_path, f"COMTRADE_API_KEY={FAKE_SECRET}\nANTHROPIC_API_KEY=paste-your-key-here\n")
    with pytest.raises(MissingKeyError) as info:
        get_key("ANTHROPIC_API_KEY", env)
    assert FAKE_SECRET not in str(info.value)
