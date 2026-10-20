"""Errors raised while loading configuration."""

from hw_checker.domain.errors import HwCheckerError


class ConfigurationError(HwCheckerError):
    """Raised when configuration files or values are missing or invalid."""
