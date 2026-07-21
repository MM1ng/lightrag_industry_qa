"""Print a sanitized, non-network environment readiness report."""

from __future__ import annotations

import platform
import sys
from importlib import metadata

from pydantic import SecretStr, ValidationError

from industrial_energy_agent.config.settings import Settings

SECRET_VARIABLES = (
    "LLM_API_KEY",
    "DASHSCOPE_API_KEY",
    "LIGHTRAG_API_KEY",
    "SERVICE_TOKEN",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
)


def _secret_status(secret: SecretStr | None) -> str:
    # Empty placeholders are normalized to ``None`` by Settings. Presence can
    # therefore be reported without ever unwrapping the secret value.
    return "SET" if secret is not None else "UNSET"


def _distribution_version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "NOT INSTALLED"


def main() -> int:
    compatible_python = sys.version_info[:2] == (3, 11)
    try:
        settings = Settings()
    except ValidationError:
        print("EnergyOps Copilot environment")
        print(f"Executable: {sys.executable}")
        print("Settings: INVALID (details suppressed)")
        return 1

    secret_statuses = {
        "LLM_API_KEY": _secret_status(settings.explicit_llm_api_key),
        "DASHSCOPE_API_KEY": _secret_status(settings.dashscope_api_key),
        "LIGHTRAG_API_KEY": _secret_status(settings.lightrag_api_key),
        "SERVICE_TOKEN": _secret_status(settings.service_token),
        "LANGFUSE_PUBLIC_KEY": _secret_status(settings.langfuse_public_key),
        "LANGFUSE_SECRET_KEY": _secret_status(settings.langfuse_secret_key),
    }

    print("EnergyOps Copilot environment")
    print(f"Executable: {sys.executable}")
    print(f"Python: {platform.python_version()}")
    print(f"Python 3.11 compatible: {'YES' if compatible_python else 'NO'}")
    print(f"Project package: {_distribution_version('energyops-copilot')}")
    print(f"Pydantic: {_distribution_version('pydantic')}")
    print(f"LLM base URL source: {settings.llm_base_url_source.upper()}")
    print("Secret configuration (values are never printed):")
    for variable_name in SECRET_VARIABLES:
        print(f"  {variable_name}: {secret_statuses[variable_name]}")
    return 0 if compatible_python else 1


if __name__ == "__main__":
    raise SystemExit(main())
