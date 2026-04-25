from app.prompts import SYSTEM_PROMPT


def test_prompt_is_french_and_names_bot() -> None:
    assert "MwanaBot" in SYSTEM_PROMPT
    assert "francais" in SYSTEM_PROMPT


def test_prompt_knows_user_is_authenticated() -> None:
    assert "deja connecte et authentifie" in SYSTEM_PROMPT
    assert "Ne dis jamais" in SYSTEM_PROMPT
