"""Configuration defaults: a working model, and no bot token in the logs."""
import logging

import bot


def test_default_model_is_a_current_openrouter_model():
    assert bot.DEFAULT_MODEL == "openai/gpt-4.1-mini"


def test_chat_uses_default_model_unless_env_overrides(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    seen = []

    def create(model, messages):
        seen.append(model)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    llm = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(bot, "save_user_message", lambda *a, **k: None)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    asyncio.run(bot.chat_with_gpt("hi", 1, llm))
    monkeypatch.setenv("OPENROUTER_MODEL", "custom/model")
    asyncio.run(bot.chat_with_gpt("hi", 1, llm))
    assert seen == [bot.DEFAULT_MODEL, "custom/model"]


def test_httpx_does_not_log_request_urls_at_info():
    # Telegram API URLs contain the bot token; httpx logs them at INFO.
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
