"""Read interfaces for the logical state layer; no separate state service."""

from ..coordinator.internal_state import get_state as get_coordinator_state
from ..executor.internal_state import get_state as get_execution_state
from .control import get_flag, get_flags

__all__ = ["get_coordinator_state", "get_execution_state", "get_flag", "get_flags"]
