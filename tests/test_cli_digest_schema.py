"""CLI compaction must preserve constraints and payload field names."""

from copy import deepcopy

from dashboard.app import _cli_schema_without_descriptions


def test_compaction_keeps_description_payload_and_validation_constraints():
    schema = {
        "type": "object", "description": "metadata", "additionalProperties": False,
        "properties": {
            "description": {"type": "string", "description": "field metadata"},
            "items": {"type": "array", "minItems": 2, "items": {
                "type": "integer", "minimum": 1, "description": "item metadata",
            }},
        },
        "required": ["description", "items"],
    }
    original = deepcopy(schema)
    compact = _cli_schema_without_descriptions(schema)
    assert schema == original
    assert "description" not in compact
    assert compact["properties"]["description"] == {"type": "string"}
    assert compact["required"] == ["description", "items"]
    assert compact["additionalProperties"] is False
    assert compact["properties"]["items"] == {
        "type": "array", "minItems": 2, "items": {"type": "integer", "minimum": 1},
    }


def test_compaction_keeps_literal_object_constraints_and_defaults():
    value = {"description": "literal payload", "nested": {"description": "also literal"}}
    schema = {
        "type": "object", "description": "schema annotation",
        "const": deepcopy(value), "enum": [deepcopy(value)],
        "default": deepcopy(value), "examples": [deepcopy(value)],
    }
    compact = _cli_schema_without_descriptions(schema)
    assert "description" not in compact
    for key in ("const", "enum", "default", "examples"):
        assert compact[key] == schema[key]
