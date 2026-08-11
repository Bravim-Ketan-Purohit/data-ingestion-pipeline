"""Schema routes: create and list target JSON schemas."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from pipeline.db.engine import get_session
from pipeline.schemas.registry import SchemaRegistry

router = APIRouter()


class CreateSchemaRequest(BaseModel):
    """Request to create a new schema."""

    name: str
    json_schema: dict[str, Any]
    description: str | None = None


@router.post("")
async def create_schema(
    request: CreateSchemaRequest,
    session: AsyncSession = Depends(get_session),
):
    """Create a new target JSON schema (or new version of existing)."""
    registry = SchemaRegistry(session)
    try:
        schema = await registry.create_schema(
            name=request.name,
            json_schema=request.json_schema,
            description=request.description,
        )
        await session.commit()
        return {
            "id": str(schema.id),
            "name": schema.name,
            "version": schema.version,
            "created_at": schema.created_at.isoformat() if schema.created_at else None,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
async def list_schemas(
    session: AsyncSession = Depends(get_session),
):
    """List all schemas (latest version of each)."""
    registry = SchemaRegistry(session)
    schemas = await registry.list_schemas()
    return [
        {
            "id": str(s.id),
            "name": s.name,
            "version": s.version,
            "description": s.description,
            "json_schema": s.json_schema,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in schemas
    ]


@router.get("/{schema_id}")
async def get_schema(
    schema_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Get a schema by ID."""
    registry = SchemaRegistry(session)
    schema = await registry.get_schema(schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Schema not found")
    return {
        "id": str(schema.id),
        "name": schema.name,
        "version": schema.version,
        "description": schema.description,
        "json_schema": schema.json_schema,
        "created_at": schema.created_at.isoformat() if schema.created_at else None,
    }
