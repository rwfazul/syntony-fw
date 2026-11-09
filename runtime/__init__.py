"""Public configuration interface for the event-time reconfiguration runtime."""

from .configuration import load_syntony_config, validate

__version__ = "0.1.0"
__all__ = ["load_syntony_config", "load_framework_config", "validate", "__version__"]

load_framework_config = load_syntony_config
