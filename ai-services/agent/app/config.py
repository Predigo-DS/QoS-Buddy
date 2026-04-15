"""
Centralized configuration loader for QoS-Buddy.

Loads provider configuration from config/providers.json and exposes
PROVIDERS, DEFAULT_PROVIDER, and LLM_MODEL for use in the application.
"""

import json
import os
from pathlib import Path
from typing import Any


def _load_providers_config() -> dict[str, Any]:
    """
    Load provider configuration from JSON file.

    Searches for providers.json in multiple locations:
    1. /app/config/providers.json (Docker volume mount)
    2. <agent_dir>/config/providers.json (local development)
    3. <project_root>/config/providers.json (fallback)

    Returns:
        dict: Parsed configuration with providers, defaults, and metadata
    """
    possible_paths = [
        Path("/app/config/providers.json"),
        Path(__file__).parent.parent / "config" / "providers.json",
        Path(__file__).parent.parent.parent / "config" / "providers.json",
        Path(__file__).parent.parent.parent.parent / "config" / "providers.json",
    ]

    config_path = None
    for path in possible_paths:
        if path.exists():
            config_path = path
            break

    if config_path is None:
        raise FileNotFoundError(
            "providers.json not found. Please create config/providers.json with provider configuration."
        )

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    return config


def _validate_provider_config(config: dict[str, Any]) -> None:
    """
    Validate provider configuration structure.

    Args:
        config: Raw configuration dictionary

    Raises:
        ValueError: If configuration is invalid
    """
    if not isinstance(config.get("providers"), dict):
        raise ValueError("Configuration must contain a 'providers' object")

    required_fields = ["base_url", "api_key_env"]
    optional_fields = ["display_name", "enabled", "description"]

    for provider_id, provider_config in config["providers"].items():
        if not isinstance(provider_config, dict):
            raise ValueError(f"Provider '{provider_id}' must be an object")

        for field in required_fields:
            if field not in provider_config:
                raise ValueError(
                    f"Provider '{provider_id}' is missing required field: '{field}'"
                )

        for field in optional_fields:
            if field in provider_config:
                if field == "enabled" and not isinstance(provider_config[field], bool):
                    raise ValueError(
                        f"Provider '{provider_id}' field 'enabled' must be a boolean"
                    )
                elif field in ["display_name", "description"]:
                    if not isinstance(provider_config[field], str):
                        raise ValueError(
                            f"Provider '{provider_id}' field '{field}' must be a string"
                        )


def load_providers() -> dict[str, Any]:
    """
    Load and process provider configuration.

    Reads providers.json, validates it, and constructs the PROVIDERS dictionary
    with actual API keys from environment variables.

    Returns:
        dict: Processed configuration with:
            - providers: Dict of provider_id -> {base_url, api_key, display_name, description, enabled}
            - default_provider: Default provider ID
            - default_model: Default model name
    """
    raw_config = _load_providers_config()
    _validate_provider_config(raw_config)

    providers = {}
    for provider_id, cfg in raw_config.get("providers", {}).items():
        if not cfg.get("enabled", True):
            continue

        api_key_env = cfg.get("api_key_env")
        api_key = os.getenv(api_key_env) if api_key_env else None

        if api_key_env and not api_key:
            print(
                f"Warning: Provider '{provider_id}' requires env var '{api_key_env}' but it's not set. "
                f"This provider will be available but may fail on use."
            )

        providers[provider_id] = {
            "base_url": cfg["base_url"],
            "api_key": api_key,
            "display_name": cfg.get("display_name", provider_id),
            "description": cfg.get("description", ""),
            "enabled": True,
            "timeout_seconds": cfg.get("timeout_seconds", 10),
        }

    return {
        "providers": providers,
        "default_provider": raw_config.get("default_provider", "local-openai"),
        "default_model": raw_config.get("default_model", "qwen35b"),
    }


# Load configuration at module import time
CONFIG = load_providers()

# Export for easy importing
PROVIDERS: dict[str, dict[str, str | None]] = CONFIG["providers"]
DEFAULT_PROVIDER: str = CONFIG["default_provider"]
LLM_MODEL: str = CONFIG["default_model"]

# Also export full config for frontend API
FULL_CONFIG: dict[str, Any] = {
    "providers": {
        pid: {
            "base_url": cfg["base_url"],
            "display_name": cfg["display_name"],
            "description": cfg["description"],
            "enabled": cfg["enabled"],
        }
        for pid, cfg in PROVIDERS.items()
    },
    "default_provider": DEFAULT_PROVIDER,
    "default_model": LLM_MODEL,
}
