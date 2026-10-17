"""Explicit providers with lazy imports, useful configuration errors, no globals."""

import os


def create_model(settings):
    name = settings.model
    if ":" in name:
        provider, name = name.split(":", 1)
    else:
        provider = (
            "anthropic"
            if name.startswith("claude")
            else "deepseek"
            if name.startswith("deepseek")
            else "openai"
        )
    base_url = settings.base_url or os.getenv("SORANIMI_BASE_URL")
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        if not os.getenv("ANTHROPIC_API_KEY"):
            raise ValueError("Set ANTHROPIC_API_KEY for Anthropic models")
        return ChatAnthropic(
            model=name,
            max_tokens=8192,
            timeout=120,
            max_retries=2,
            **({"base_url": base_url} if base_url else {}),
        )
    if provider not in {"openai", "deepseek"}:
        raise ValueError("Supported providers: openai, anthropic, deepseek")
    from langchain_openai import ChatOpenAI

    env_key = "DEEPSEEK_API_KEY" if provider == "deepseek" else "OPENAI_API_KEY"
    key = os.getenv("SORANIMI_API_KEY") or os.getenv(env_key)
    if not key:
        raise ValueError(f"Set {env_key} or SORANIMI_API_KEY")
    if provider == "deepseek" and not base_url:
        base_url = "https://api.deepseek.com"
    return ChatOpenAI(
        model=name, api_key=key, base_url=base_url, timeout=120, max_retries=2, streaming=True
    )
