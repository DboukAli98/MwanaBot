from app.prompts import SYSTEM_PROMPT


def test_prompt_is_french_and_names_bot() -> None:
    assert "MwanaBot" in SYSTEM_PROMPT
    assert "français" in SYSTEM_PROMPT

