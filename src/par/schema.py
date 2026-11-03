"""A small JSON-Schema checker (standard library only) for the Kernel's own documents.

Supports the keywords the shipped schemas use: type (single or list), enum, const, required, properties,
additionalProperties, items, minItems/maxItems, uniqueItems, minLength/maxLength, pattern, minimum/maximum
and local ``#/definitions/...`` references. Unknown keywords are ignored, like a draft-07 validator would.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources
from typing import Any


def _is(value: Any, kind: str) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "null":
        return value is None
    return False


def _same(a: Any, b: Any) -> bool:
    return type(a) is type(b) and a == b


def _resolve(ref: str, root: dict) -> dict:
    if not ref.startswith("#/"):
        raise ValueError(f"only local references are supported: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def validate(instance: Any, schema: dict, root: "dict | None" = None, path: str = "$") -> "list[str]":
    """Return a list of human-readable problems; an empty list means the instance is valid."""
    root = root or schema
    if "$ref" in schema:
        return validate(instance, _resolve(schema["$ref"], root), root, path)
    errors: "list[str]" = []
    if "const" in schema and not _same(instance, schema["const"]):
        return [f"{path}: must be {schema['const']!r}"]
    if "enum" in schema and not any(_same(instance, v) for v in schema["enum"]):
        return [f"{path}: must be one of {schema['enum']}"]
    if "type" in schema:
        kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is(instance, k) for k in kinds):
            return [f"{path}: expected {' or '.join(kinds)}, found {type(instance).__name__}"]
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: does not match {schema['pattern']}")
    if _is(instance, "integer") or _is(instance, "number"):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: below the minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: above the maximum {schema['maximum']}")
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in instance}) != len(instance):
            errors.append(f"{path}: items must be unique")
        if "items" in schema:
            for i, item in enumerate(instance):
                errors += validate(item, schema["items"], root, f"{path}[{i}]")
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required '{key}'")
        props = schema.get("properties", {})
        for key, value in instance.items():
            if key in props:
                errors += validate(value, props[key], root, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected '{key}'")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors += validate(value, schema["additionalProperties"], root, f"{path}.{key}")
    return errors


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict:
    text = resources.files("par").joinpath("schemas", f"{name}.schema.json").read_text(encoding="utf-8")
    return json.loads(text)


def check(instance: Any, name: str) -> "list[str]":
    """Validate against one of the shipped schemas: ``task``, ``result`` or ``event``."""
    return validate(instance, load_schema(name))
