"""Helpers shared by provider adapters."""

# FORGE tool names are dotted ("metrics.read"); provider function names allow only
# [a-zA-Z0-9_-]. Tool names may not contain "__" (ToolDefinition enforces it), so
# the mapping is reversible.


def encode_tool_name(name: str) -> str:
    return name.replace(".", "__")


def decode_tool_name(name: str) -> str:
    return name.replace("__", ".")


def is_retryable_status(status_code: int) -> bool:
    """Request timeout, conflict, rate limit, and server errors are transient."""
    return status_code in {408, 409, 429} or status_code >= 500
