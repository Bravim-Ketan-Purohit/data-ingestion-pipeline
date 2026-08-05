"""Structured logging configuration.

RULES (from CLAUDE.md):
- Never log presigned URLs (they ARE credentials)
- Never log document contents (client data by premise)
- Always include document_id, partition_id, field_path where relevant
"""

import structlog

from pipeline.config import settings


def configure_logging() -> None:
    """Configure structlog for JSON structured logging."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer() if settings.log_level != "DEBUG" else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(structlog, settings.log_level.upper(), structlog.INFO) if hasattr(structlog, settings.log_level.upper()) else 20
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a structured logger for a module."""
    return structlog.get_logger(name)
