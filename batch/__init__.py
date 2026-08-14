"""Batch tier: Spark + Delta Lake + Airflow + dbt.

Genuinely separate from the interactive tier. Shares the schema registry and
extraction prompts, nothing else.

Unit of work: a corpus of thousands (>= 5000 documents), nobody waiting.
Verification: statistical sampling + automated quality gates (dbt tests).
Failure handling: quarantine and continue.
"""
