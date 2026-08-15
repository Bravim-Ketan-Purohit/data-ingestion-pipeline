"""Kafka consumer for document arrival events.

The batch tier consumes documents.landed events to trigger processing.
This decouples arrival from processing, and gives the Airflow sensor
something real to sense.
"""

import json
from typing import AsyncGenerator, Callable

from pipeline.config import settings
from pipeline.observability.logging import get_logger

logger = get_logger(__name__)

TOPIC = "documents.landed"
GROUP_ID = "batch-pipeline"


class DocumentEventConsumer:
    """Consumes document.landed events for the batch tier."""

    def __init__(self) -> None:
        self._consumer = None

    async def _ensure_consumer(self):
        """Lazy-initialize the Kafka consumer."""
        if self._consumer is None:
            from aiokafka import AIOKafkaConsumer

            self._consumer = AIOKafkaConsumer(
                TOPIC,
                bootstrap_servers=settings.kafka_bootstrap_servers,
                group_id=GROUP_ID,
                value_deserializer=lambda v: json.loads(v.decode("utf-8")),
                auto_offset_reset="earliest",
                enable_auto_commit=True,
            )
            await self._consumer.start()

    async def consume_events(
        self, handler: Callable, max_messages: int | None = None
    ) -> int:
        """Consume events and pass to handler.

        Args:
            handler: Async callable that processes each event
            max_messages: Stop after N messages (for testing/micro-batches)

        Returns:
            Number of messages processed
        """
        await self._ensure_consumer()
        count = 0

        async for message in self._consumer:
            event = message.value
            logger.info(
                "kafka_event_consumed",
                topic=message.topic,
                offset=message.offset,
                document_id=event.get("document_id"),
            )

            await handler(event)
            count += 1

            if max_messages and count >= max_messages:
                break

        return count

    async def close(self) -> None:
        """Close the consumer."""
        if self._consumer:
            await self._consumer.stop()
