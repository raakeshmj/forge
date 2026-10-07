"""JSON Schema validation of tool schemas and model-produced arguments (ADR 0005)."""

from collections.abc import Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from forge.domain.common import JSONValue
from forge.domain.tool import ToolDefinition

_MAX_REPORTED_ERRORS = 5


def schema_problem(definition: ToolDefinition) -> str | None:
    """Why the tool's input schema is unusable, or None if it is valid."""
    try:
        Draft202012Validator.check_schema(dict(definition.input_schema))
    except SchemaError as exc:
        return f"invalid input schema: {exc.message}"
    if definition.input_schema.get("type") != "object":
        return "input schema must describe an object"
    return None


def argument_errors(definition: ToolDefinition, arguments: Mapping[str, JSONValue]) -> list[str]:
    validator = Draft202012Validator(dict(definition.input_schema))
    errors = sorted(validator.iter_errors(dict(arguments)), key=lambda e: list(e.absolute_path))
    return [
        f"{'/'.join(str(p) for p in error.absolute_path) or '<root>'}: {error.message}"
        for error in errors[:_MAX_REPORTED_ERRORS]
    ]
