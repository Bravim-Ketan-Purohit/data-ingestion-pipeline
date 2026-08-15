"""Kafka event bus: document arrival notifications.

Kafka carries EVENTS, never file bytes. Documents move through S3 and Delta.
Putting file bytes in Kafka is a common and costly mistake.
"""
