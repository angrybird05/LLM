
from coding_agent.config import settings
from coding_agent.core.llm.anthropic import AnthropicProvider
from coding_agent.core.llm.base import LLMProvider
from coding_agent.core.llm.openai import OpenAIProvider

_providers: dict[str, LLMProvider] = {}


def get_provider(name: str | None = None) -> LLMProvider:
    provider_name = name or settings.DEFAULT_PROVIDER
    if provider_name not in _providers:
        if provider_name == "anthropic":
            _providers[provider_name] = AnthropicProvider()
        elif provider_name in ("openai", "openrouter"):
            _providers[provider_name] = OpenAIProvider(via=provider_name)
        else:
            raise ValueError(f"Unknown provider: {provider_name}")
    return _providers[provider_name]


def list_providers() -> dict[str, list[str]]:
    """Static fallback suggestions per provider."""
    return {
        "anthropic": AnthropicProvider().models,
        "openai": OpenAIProvider(via="openai").models,
        "openrouter": OpenAIProvider(via="openrouter").models,
    }


async def list_models(provider_name: str | None = None) -> list[str]:
    """Live model ids for a provider; any returned id may be passed as
    `model` to the chat paths. Falls back to static suggestions offline."""
    provider = get_provider(provider_name)
    try:
        models = await provider.list_models()
    except Exception:
        models = []
    return models or provider.models
