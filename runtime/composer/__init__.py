"""Domain-local observed-event composition and temporal admission."""

from .composer import WatermarkManager, composer_loop, match_rule
from .rules import get_ruleset

__all__ = ["WatermarkManager", "match_rule", "composer_loop", "get_ruleset"]
