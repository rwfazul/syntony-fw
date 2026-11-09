"""Stable-round correlation and coordination-event generation."""

from .cep import cep_loop, close_stable_rounds, close_stable_ticks, effective_watermark
from .patterns import _compile_pattern as compile_pattern

__all__ = [
    "effective_watermark",
    "close_stable_rounds",
    "close_stable_ticks",
    "compile_pattern",
    "cep_loop",
]
