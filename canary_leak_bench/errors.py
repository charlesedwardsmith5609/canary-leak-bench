"""Error types. Messages must never include a canary or an API key."""


class BenchError(Exception):
    """Base error for the harness."""


class ConfigError(BenchError):
    """The operator's config or flags are invalid."""


class ScenarioError(BenchError):
    """The scenario catalog failed validation."""


class ForumError(BenchError):
    """The fake forum or a call to it failed."""


class ProviderError(BenchError):
    """A model API call failed. The message is a fixed phrase, not the response body."""
