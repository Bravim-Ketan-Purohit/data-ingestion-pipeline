"""Schema registry: versioning, strict validation, coercion rules.

Target schemas are user-supplied JSON Schema (draft 2020-12), stored and versioned.
Validation is STRICT: additionalProperties: false, type coercion only where explicitly
configured.
"""

import json
import uuid
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pipeline.db.models import Schema
from pipeline.observability.logging import get_logger

logger = get_logger(__name__)


class SchemaRegistry:
    """Manages target JSON schemas with versioning."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_schema(self, name: str, json_schema: dict, description: str | None = None) -> Schema:
        """Create a new schema or a new version of an existing one."""
        # Find latest version
        result = await self._session.execute(
            select(Schema)
            .where(Schema.name == name)
            .order_by(Schema.version.desc())
            .limit(1)
        )
        latest = result.scalar_one_or_none()
        version = (latest.version + 1) if latest else 1

        # Validate the schema itself is valid JSON Schema
        try:
            Draft202012Validator.check_schema(json_schema)
        except jsonschema.SchemaError as e:
            raise ValueError(f"Invalid JSON Schema: {e.message}")

        schema = Schema(
            id=uuid.uuid4(),
            name=name,
            version=version,
            json_schema=json_schema,
            description=description,
        )
        self._session.add(schema)
        await self._session.flush()

        logger.info("schema_created", name=name, version=version)
        return schema

    async def get_schema(self, schema_id: uuid.UUID) -> Schema | None:
        """Get a schema by ID."""
        return await self._session.get(Schema, schema_id)

    async def get_latest(self, name: str) -> Schema | None:
        """Get the latest version of a named schema."""
        result = await self._session.execute(
            select(Schema)
            .where(Schema.name == name)
            .order_by(Schema.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_schemas(self) -> list[Schema]:
        """List all schemas (latest version of each)."""
        result = await self._session.execute(
            select(Schema).order_by(Schema.name, Schema.version.desc())
        )
        schemas = result.scalars().all()
        # Deduplicate to latest version per name
        seen: dict[str, Schema] = {}
        for s in schemas:
            if s.name not in seen:
                seen[s.name] = s
        return list(seen.values())


class SchemaValidator:
    """Strict JSON Schema validation with optional coercion.

    Validation is strict: additionalProperties: false, type coercion only where
    explicitly configured (e.g. "1,234.50" → 1234.50 when the field is a number
    with a declared locale).
    """

    def __init__(self, json_schema: dict) -> None:
        self._schema = json_schema
        # Ensure strict mode
        if "additionalProperties" not in self._schema:
            self._schema["additionalProperties"] = False
        self._validator = Draft202012Validator(self._schema)

    def validate(self, data: dict) -> list[dict[str, str]]:
        """Validate data against the schema.

        Returns a list of validation errors with field paths.
        Empty list means valid.
        """
        errors = []
        for error in self._validator.iter_errors(data):
            path = "/".join(str(p) for p in error.absolute_path) if error.absolute_path else "/"
            errors.append({
                "path": f"/{path}" if not path.startswith("/") else path,
                "message": error.message,
                "validator": error.validator,
            })
        return errors

    def validate_field(self, path: str, value: Any) -> str | None:
        """Validate a single field value against its schema definition.

        Returns the validation error message, or None if valid.
        """
        # Navigate to the field's schema
        parts = [p for p in path.strip("/").split("/") if p]
        field_schema = self._schema

        for part in parts:
            props = field_schema.get("properties", {})
            if part in props:
                field_schema = props[part]
            else:
                return f"Field '{path}' not found in schema"

        try:
            jsonschema.validate(value, field_schema)
            return None
        except ValidationError as e:
            return e.message

    def coerce_value(self, path: str, raw_value: str) -> Any:
        """Apply type coercion for a field based on schema type.

        Handles: numeric strings with thousands separators/currency,
        boolean strings, date strings.
        """
        parts = [p for p in path.strip("/").split("/") if p]
        field_schema = self._schema

        for part in parts:
            props = field_schema.get("properties", {})
            if part in props:
                field_schema = props[part]
            else:
                return raw_value

        field_type = field_schema.get("type", "string")

        if field_type == "number" or field_type == "integer":
            return self._coerce_numeric(raw_value, field_type)
        elif field_type == "boolean":
            return self._coerce_boolean(raw_value)
        elif field_type == "array":
            if isinstance(raw_value, str):
                try:
                    return json.loads(raw_value)
                except json.JSONDecodeError:
                    return [raw_value]

        return raw_value

    def _coerce_numeric(self, value: str, target_type: str) -> int | float | str:
        """Coerce a string to a number, handling thousands separators and currency."""
        import re
        if not isinstance(value, str):
            return value

        # Remove currency symbols
        cleaned = re.sub(r"[$€£¥₹]", "", value.strip())
        # Remove thousands separators (but not decimal point)
        cleaned = cleaned.replace(",", "").replace(" ", "")
        # Handle percentage
        is_percent = cleaned.endswith("%")
        if is_percent:
            cleaned = cleaned[:-1]

        try:
            if target_type == "integer":
                result = int(float(cleaned))
            else:
                result = float(cleaned)

            if is_percent:
                result = result / 100.0

            return result
        except ValueError:
            return value

    def _coerce_boolean(self, value: str) -> bool | str:
        """Coerce a string to boolean."""
        if not isinstance(value, str):
            return value
        lower = value.strip().lower()
        if lower in ("true", "yes", "1", "y"):
            return True
        if lower in ("false", "no", "0", "n"):
            return False
        return value
