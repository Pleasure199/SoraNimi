"""Explicit providers with lazy imports, useful configuration errors, no globals."""

import os
from dataclasses import replace
from functools import cached_property


def create_model(settings, *, use_env_base_url=True):
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
    base_url = settings.base_url or (os.getenv("SORANIMI_BASE_URL") if use_env_base_url else None)
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


class SummaryModel:
    """Create an optional separate client only when compaction is needed."""

    def __init__(self, settings, fallback, factory=None):
        self.settings = settings
        self.fallback = fallback
        self.factory = factory or (lambda selected: create_model(selected, use_env_base_url=False))

    @cached_property
    def client(self):
        if not self.settings.summary_model:
            return self.fallback
        # A main-model custom endpoint may belong to another provider. Summary
        # endpoints are explicit, otherwise create_model uses provider defaults.
        return self.factory(
            replace(
                self.settings,
                model=self.settings.summary_model,
                base_url=self.settings.summary_base_url,
            )
        )

    def invoke(self, messages, config=None):
        return self.client.invoke(messages, config=config)
