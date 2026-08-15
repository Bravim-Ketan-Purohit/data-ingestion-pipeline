"""Kafka producer for document arrival events.

Topic: documents.landed
Payload: Protobuf-encoded event (NOT file bytes).
Kafka is an event notification bus here, not the data path.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from pipeline.config import settings
from pipeline.observability.logging import get_logger

logger = get_logger(__name__)

TOPIC = "documents.landed"


class DocumentEventProducer:
    """Publishes document arrival events to Kafka.

    Events are published when a document completes upload (interactive tier)
    or is detected in the landing zone (batch tier).
    The batch tier consumes these to trigger micro-batches.
    The interactive tier ignores them.
    """

    def __init__(self) -> None:
        self._producer = None

    async def _ensure_producer(self):
        """Lazy-initialize the Kafka producer."""
        if self._producer is None:
            from aiokafka import AIOKafkaProducer

            self._producer = AIOKafkaProducer(
                bootstrap_servers=settings.kafka_bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
            )
            await self._producer.start()

    async def publish_document_landed(
        self,
        document_id: str,
        content_hash: str,
        filename: str,
        mime: str,
        size_bytes: int,
        s3_key: str,
        schema_name: str,
    ) -> None:
        """Publish a document.landed event.

        This event carries metadata only — never file bytes.
        The document itself is in S3.
        """
        await self._ensure_producer()

        event = {
            "event_type": "document.landed",
            "event_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "document_id": document_id,
            "content_hash": content_hash,
            "filename": filename,
            "mime": mime,
            "size_bytes": size_bytes,
            "s3_key": s3_key,
            "schema_name": schema_name,
        }

        await self._producer.send_and_wait(
            topic=TOPIC,
            key=document_id,
            value=event,
        )

        logger.info(
            "kafka_event_published",
            topic=TOPIC,
            document_id=document_id,
            event_type="document.landed",
        )

    async def close(self) -> None:
        """Close the producer."""
        if self._producer:
            await self._producer.stop()
