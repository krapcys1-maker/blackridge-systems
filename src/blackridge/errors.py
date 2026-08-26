"""Domain-specific failures exposed by the Blackridge CLI."""


class BlackridgeError(RuntimeError):
    """Base class for expected, user-facing Blackridge failures."""


class ExternalToolError(BlackridgeError):
    """An upstream CLI failed or returned an invalid response."""


class ConfigurationError(BlackridgeError):
    """A system request or local tool configuration is invalid."""
