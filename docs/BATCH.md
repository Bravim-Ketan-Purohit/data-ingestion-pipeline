# docs/BATCH.md — Batch Tier Design

## Overview

The batch tier processes ≥ 5,000 documents through Bronze → Silver → Gold using PySpark and Delta Lake.
It is **genuinely separate** from the interactive tier. They share the schema registry and extraction
prompts, nothing else.

| | Interactive tier | Batch tier |
|---|---|---|
| Unit of work | One document, a human waiting | Corpus of thousands, nobody waiting |
| Verification | Operator verifies every field | Statistical sampling + dbt quality gate |
| Latency | Seconds | Minutes to hours |
| Failure handling | Surface to operator | Quarantine and continue |
| Runtime | FastAPI + Claude API | Spark + Airflow + Delta Lake |

---

## Executor-Side Rate Limiting

### The problem

Naive `mapPartitions` with N executors blows through Claude's global rate limit. Each executor makes
independent API calls with no knowledge of what others are doing. With 4 executors each making 50
RPM, you're hitting 200 RPM against a 50 RPM limit.

### Strategy chosen: Per-executor token bucket

**Approach:** Divide the global rate limit evenly across executors. Each executor gets
`global_limit / num_executors` tokens per minute.

```
Global RPM limit: 50
Executors: 4
Per-executor allocation: 12.5 RPM (one request every ~5 seconds)
```

**Implementation:** `batch/spark/rate_limit_strategy.py` — `ExecutorRateLimiter` class.

### Trade-offs

| Aspect | Per-executor token bucket | Two-stage design (alternative) |
|---|---|---|
| Complexity | Simple. Each executor is self-contained. | More complex. Spark prepares batches, separate async worker calls API. |
| Coordination | None needed between executors. | Requires a coordination layer (queue, shared state). |
| Utilization | Under-utilizes if some executors are idle. Equal split doesn't adapt to skew. | Can utilize full quota regardless of which executors are active. |
| Failure mode | One slow executor doesn't affect others. | Queue backup can cascade. |
| Implementation | Pure Python in mapPartitions. | Requires external queue (Kafka/Redis) and a separate service. |
| Monitoring | Per-executor metrics only. | Centralized view of quota usage. |

**Why per-executor wins here:**
1. Simpler to reason about and debug
2. No additional infrastructure (no queue, no coordination service)
3. Spark already handles the parallelism — adding another layer of async adds complexity without
   proportional benefit
4. The "idle executor" problem is partially solved by the skew handler, which ensures partitions are
   roughly balanced by processing time

### When to reconsider

Switch to the two-stage design if:
- The corpus requires burst capacity (all documents need extraction in < 10 minutes)
- Executor count varies dynamically (autoscaling cluster)
- Multiple Spark jobs share the same API key simultaneously

---

## Partition Skew

### The problem

A 400-page PDF takes 10x longer to process than a 2-page CSV. Without intervention, one executor gets
stuck on the large document while others sit idle.

### Solution: Size-based repartitioning

**Implementation:** `batch/spark/skew_handler.py`

1. Assign a weight to each document based on file size
2. Salt large documents to spread processing across partitions
3. Target roughly equal total weight per partition

```python
weight = 10 if size > 10MB else 3 if size > 1MB else 1
partitions = max(4, total_weight / 3)
```

### Metrics (before/after)

Reported in the Airflow task logs and recorded in the pipeline metrics:

| Metric | Before repartitioning | After repartitioning |
|---|---|---|
| Skew ratio (max/median docs per partition) | TBD (depends on corpus) | Target: < 2.0 |
| Stage wall-clock time | TBD | TBD |
| Idle executor time | TBD | Target: < 10% |

---

## Delta Lake Features Used

These are the features that justify choosing Delta over plain Parquet:

### 1. MERGE-based reprocessing (Silver)

When a prompt changes, re-running extraction must **update** existing rows, not duplicate them.

```sql
MERGE INTO silver USING new_extractions
ON silver.content_hash = new.content_hash AND silver.field_path = new.field_path
WHEN MATCHED AND silver.prompt_hash != new.prompt_hash THEN UPDATE SET ...
WHEN NOT MATCHED THEN INSERT *
```

This is the operation plain Parquet cannot do.

### 2. Schema evolution (Silver)

A new field in the target schema must not require backfilling everything. Silver uses
`mergeSchema` deliberately (not as a default) when the schema legitimately changes.

### 3. Time-travel provenance (Gold)

Every Gold record stores `silver_delta_version` — the Delta version of Silver it was derived from.
To reproduce a result: `spark.read.format("delta").option("versionAsOf", version).load(silver_path)`.

### 4. OPTIMIZE / ZORDER / VACUUM

- **OPTIMIZE + ZORDER** on columns actually filtered (`document_id`, `schema_name`, `ingest_date`)
- **VACUUM** with 168-hour retention
- Without these, a lakehouse becomes a small-file problem with extra dependencies

---

## Corpus

The batch corpus is ≥ 5,000 documents, synthesized by mutating a seed corpus. This ensures Spark is
the honest choice — a Spark job over 30 documents demonstrates Spark rather than uses it.

**Synthesis strategy:**
- Start with a seed of ~50 real-world-shaped documents (multi-page PDFs with tables, messy CSVs)
- Generate variations: date format changes, value mutations, layout shifts, noise injection
- Each variant gets a unique content_hash
- Target: 5,000+ documents across 3+ schema shapes

**Corpus size matters because:**
- Single-machine pandas handles 30 documents trivially
- Spark's overhead only pays off at scale (partition distribution, shuffle, task scheduling)
- The honest claim is "handles volume, not just documents"

---

## Kafka Events

**Topic:** `documents.landed`

**Payload:** Metadata only (document_id, content_hash, s3_key, schema_name, etc.)

**What Kafka is NOT:**
- NOT the data path. Documents move through S3 and Delta.
- NOT carrying file bytes. Putting document content in Kafka is a common and costly mistake.

**What Kafka IS:**
- An event notification bus that decouples arrival from processing
- The trigger mechanism for Airflow sensors
- An audit log of document arrivals

---

## Airflow DAG

**Pipeline:** `land → validate → partition → extract → conform → quality_gate → publish`

**Key requirements:**
- **Idempotent tasks:** MERGE-based operations ensure re-runs don't duplicate
- **Backfill:** `catchup=True` with date-partitioned Bronze
- **Retries:** Exponential backoff (3 retries, 5-30 minute delay)
- **SLA:** 6-hour window, alerting on miss
- **Cost accounting:** Per-task XCom tracking of extraction costs

**Airflow submits and tracks; Spark is the parallelism.**

---

## dbt Quality Gate

dbt tests BLOCK publish to Gold. This gate replaces the interactive tier's human verification.

**Tests:**
- `not_null` on field_id, document_id, content_hash
- `unique` on field_id and Gold document_id
- `assert_confidence_in_range` — confidence between 0.0 and 1.0
- `assert_no_orphan_fields` — every field has a valid document
- `assert_quality_score_threshold` — Gold records meet minimum quality (0.5)

**A failing test is not advisory.** The Airflow task raises an exception and blocks the pipeline.

---

## Running Locally

```bash
# Start batch services
docker compose -f docker-compose.dev.yml --profile batch up -d

# Run the full pipeline
python -m batch.spark.bronze_ingest  # or via Airflow
python -m batch.spark.silver_extract
python -m batch.spark.gold_conform

# Run dbt tests
cd batch/dbt && dbt test --profiles-dir . --project-dir .

# Maintenance
python -m batch.spark.maintenance
```

No Databricks account required. Results from local Spark + Delta are labeled as such.
