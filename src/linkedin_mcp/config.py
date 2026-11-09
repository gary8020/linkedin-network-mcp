from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_ENV_VAR = "LINKEDIN_NETWORK_MCP_CONFIG"
DEFAULT_CONFIG_PATH = Path.home() / ".linkedin-network-mcp" / "config.toml"

DEFAULT_WARM_LIST_KEYWORDS: tuple[str, ...] = (
    "saas",
    "agency",
    "agencies",
    "3pl",
    "amazon",
    "shopify",
    "ecommerce",
    "e-commerce",
    "partnership",
    "marketing",
    "sponsorship",
    "founder",
    "cofounder",
    "ceo",
    "head of",
)

DEFAULT_WARM_LIST_WEIGHTS: dict[str, float] = {
    "keyword": 0.40,
    "dm_history": 0.30,
    "endorsements": 0.15,
    "connected_recency": 0.15,
}

DEFAULT_CONFIG_TOML = """\
# linkedin-network-mcp configuration. Read on every warm_list call; no restart needed.

[warm_list]
# Case-insensitive. A keyword matches company or title text where it starts at a
# word boundary, so "partnership" also matches "Partnerships" and "founder" matches
# "Co-Founder". Japanese keywords match as plain substrings.
keywords = [
  "saas", "agency", "agencies", "3pl", "amazon", "shopify", "ecommerce", "e-commerce",
  "partnership", "marketing", "sponsorship", "founder", "cofounder", "ceo", "head of",
]

# Number of distinct keyword hits that earns the full keyword component.
keyword_saturation = 2

# Connected-date component is exp(-days_since_connected / connected_decay_days).
connected_decay_days = 1095

[warm_list.weights]
# Relative weights; they are normalized, so they do not need to sum to 1.
keyword = 0.40            # keyword hits in company + title
dm_history = 0.30         # log-scaled DM volume, both directions
endorsements = 0.15       # endorsements given or received (5+ = full)
connected_recency = 0.15  # how recently you connected
"""


@dataclass(frozen=True)
class WarmListConfig:
    keywords: tuple[str, ...] = DEFAULT_WARM_LIST_KEYWORDS
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WARM_LIST_WEIGHTS))
    keyword_saturation: int = 2
    connected_decay_days: float = 1095.0


@dataclass(frozen=True)
class AppConfig:
    warm_list: WarmListConfig = field(default_factory=WarmListConfig)
    source: str | None = None


def resolve_config_path(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser()
    env_value = os.environ.get(CONFIG_ENV_VAR)
    if env_value:
        return Path(env_value).expanduser()
    return DEFAULT_CONFIG_PATH


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = resolve_config_path(path)
    if not config_path.exists():
        return AppConfig()
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)
    return AppConfig(warm_list=parse_warm_list(data.get("warm_list", {})), source=str(config_path))


def parse_warm_list(section: object) -> WarmListConfig:
    if not isinstance(section, dict):
        raise ValueError("[warm_list] must be a table")
    defaults = WarmListConfig()

    keywords = section.get("keywords", list(defaults.keywords))
    if not isinstance(keywords, list) or not all(isinstance(item, str) for item in keywords):
        raise ValueError("warm_list.keywords must be a list of strings")
    cleaned_keywords = tuple(dict.fromkeys(item.strip() for item in keywords if item.strip()))

    weights = dict(defaults.weights)
    raw_weights = section.get("weights", {})
    if not isinstance(raw_weights, dict):
        raise ValueError("[warm_list.weights] must be a table")
    for name, value in raw_weights.items():
        if name not in DEFAULT_WARM_LIST_WEIGHTS:
            raise ValueError(
                f"Unknown warm_list weight {name!r}; expected one of {sorted(DEFAULT_WARM_LIST_WEIGHTS)}"
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"warm_list.weights.{name} must be a non-negative number")
        weights[name] = float(value)
    if sum(weights.values()) <= 0:
        raise ValueError("At least one warm_list weight must be greater than 0")

    saturation = section.get("keyword_saturation", defaults.keyword_saturation)
    if isinstance(saturation, bool) or not isinstance(saturation, int) or saturation < 1:
        raise ValueError("warm_list.keyword_saturation must be an integer >= 1")

    decay = section.get("connected_decay_days", defaults.connected_decay_days)
    if isinstance(decay, bool) or not isinstance(decay, (int, float)) or decay <= 0:
        raise ValueError("warm_list.connected_decay_days must be a positive number")

    return WarmListConfig(
        keywords=cleaned_keywords,
        weights=weights,
        keyword_saturation=saturation,
        connected_decay_days=float(decay),
    )


def write_default_config(path: str | Path | None = None, *, force: bool = False) -> Path:
    config_path = resolve_config_path(path)
    if config_path.exists() and not force:
        raise FileExistsError(f"{config_path} already exists; pass --force to overwrite")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    return config_path
