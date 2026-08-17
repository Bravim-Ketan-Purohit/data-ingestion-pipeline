"""Tests for schema validation and commit gate."""

import pytest

from pipeline.schemas.registry import SchemaValidator


@pytest.fixture
def sample_schema():
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
            "email": {"type": "string", "format": "email"},
            "active": {"type": "boolean"},
        },
        "required": ["name", "age"],
        "additionalProperties": False,
    }


class TestSchemaValidator:
    """Test strict JSON Schema validation."""

    def test_valid_document(self, sample_schema):
        validator = SchemaValidator(sample_schema)
        errors = validator.validate({"name": "Alice", "age": 30, "email": "a@b.com", "active": True})
        assert errors == []

    def test_missing_required_field(self, sample_schema):
        validator = SchemaValidator(sample_schema)
        errors = validator.validate({"name": "Alice"})
        assert len(errors) > 0
        assert any("age" in e["message"] for e in errors)

    def test_additional_properties_rejected(self, sample_schema):
        validator = SchemaValidator(sample_schema)
        errors = validator.validate({"name": "Alice", "age": 30, "extra": "not allowed"})
        assert len(errors) > 0

    def test_wrong_type(self, sample_schema):
        validator = SchemaValidator(sample_schema)
        errors = validator.validate({"name": "Alice", "age": "not a number"})
        assert len(errors) > 0

    def test_validate_single_field(self, sample_schema):
        validator = SchemaValidator(sample_schema)
        assert validator.validate_field("/name", "Alice") is None
        assert validator.validate_field("/age", "thirty") is not None

    def test_coerce_numeric(self, sample_schema):
        validator = SchemaValidator(sample_schema)
        assert validator.coerce_value("/age", "30") == 30
        assert validator.coerce_value("/age", "$1,234") == 1234

    def test_coerce_boolean(self, sample_schema):
        validator = SchemaValidator(sample_schema)
        assert validator.coerce_value("/active", "yes") is True
        assert validator.coerce_value("/active", "no") is False
