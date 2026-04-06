"""Configuration loading for wickd-proxy."""

import logging
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BudgetConfig:
    per_run: Optional[float] = None
    daily: Optional[float] = None
    monthly: Optional[float] = None


@dataclass
class ProviderConfig:
    api_key: Optional[str] = None
    base_url: Optional[str] = None


@dataclass
class ProxyConfig:
    port: int = 4319
    host: str = "127.0.0.1"
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    providers: dict[str, ProviderConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "ProxyConfig":
        budget_data = data.get("budget", {})
        budget = BudgetConfig(
            per_run=budget_data.get("per_run"),
            daily=budget_data.get("daily"),
            monthly=budget_data.get("monthly"),
        )
        providers = {}
        for name, pdata in data.get("providers", {}).items():
            api_key = pdata.get("api_key", "")
            # Resolve env var references like ${OPENAI_API_KEY}
            if isinstance(api_key, str) and api_key.startswith("${") and api_key.endswith("}"):
                env_var = api_key[2:-1]
                api_key = os.environ.get(env_var, "")
                if not api_key:
                    logging.getLogger("wickd").warning(
                        "Environment variable %s not set for provider %s", env_var, name
                    )
            providers[name] = ProviderConfig(api_key=api_key, base_url=pdata.get("base_url"))

        return cls(
            port=data.get("port", 4319),
            host=data.get("host", "127.0.0.1"),
            budget=budget,
            providers=providers,
        )

    @classmethod
    def from_yaml(cls, path: str) -> "ProxyConfig":
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls.from_dict(data)

    @classmethod
    def from_args(cls, **kwargs) -> "ProxyConfig":
        budget = BudgetConfig(
            per_run=kwargs.get("budget_per_run"),
            daily=kwargs.get("budget_daily"),
            monthly=kwargs.get("budget_monthly"),
        )
        return cls(
            port=kwargs.get("port", 4319),
            host=kwargs.get("host", "127.0.0.1"),
            budget=budget,
        )
