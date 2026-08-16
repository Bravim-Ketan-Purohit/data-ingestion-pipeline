# COMPLIANCE.md — Data Ingestion Pipeline

**Designed against SOC 2 and HIPAA control boundaries.** This document describes the security and
privacy controls implemented in this system. It does not assert compliance with any standard —
compliance is an audit outcome, not a code property.

---

## Not claimed

The following would be required for actual certification and are not present:

- Formal risk assessment by a qualified assessor
- Independent SOC 2 Type II audit
- BAA (Business Associate Agreement) execution
- Formal incident response plan and testing
- Annual penetration testing by a third party
- Employee security awareness training program
- Formal change management review board
- Disaster recovery testing with documented RTO/RPO

**Asserting SOC 2 or HIPAA compliance without an audit is a misrepresentation that ends
healthcare-adjacent hiring processes.** This system is designed with these control boundaries in mind;
it has not been certified against them.

---

## Data classification

| Classification | Examples | Controls |
|---|---|---|
| Confidential | Client documents (PDFs, CSVs), extracted fields | Encryption at rest + in transit, access logging, no content in logs |
| Internal | Schema definitions, extraction prompts, model versions | Access control, versioning |
| Public | README, API documentation, schema examples | None required |

## Client data handling

- **No real client data in this repository.** All corpora are synthetic or publicly sourced.
- **No document contents in logs or span attributes.** The data is client data by premise. Only
  `document_id`, `partition_id`, `field_path`, and operational metadata appear in logs and traces.
- **Presigned URLs are never logged.** A presigned URL is a credential — it grants temporary access
  to a specific object without authentication.
- **Content-hash deduplication** means the system never stores duplicate copies of the same document.

## Encryption

### At rest

**Two layers of encryption:**

1. **Client-side envelope encryption (KMS):** Documents are encrypted with a unique AES-256-GCM data
   key before upload to S3. The data key is itself encrypted by a KMS Customer Managed Key (CMK).
   This means document bytes are unreadable even with direct bucket access — the KMS key is required
   for decryption.

2. **Server-side encryption (SSE-KMS):** The S3 bucket enforces SSE-KMS as a second layer. Every
   object is encrypted at the storage level with the same CMK.

**Key rotation:** KMS CMK has automatic annual rotation enabled. Data keys are unique per document.

### In transit

- All API communication over TLS 1.2+
- Presigned URLs use HTTPS (HTTP requests are rejected)
- Internal service communication within the Kubernetes cluster uses mTLS where available
- S3 bucket policy enforces `aws:SecureTransport`

## Access control

- **No public bucket.** All S3 access is via presigned URLs with short expiry (1 hour default).
- **CORS scoped to the application origin only.** Not `*`.
- **IAM principle of least privilege:** the pipeline role has only the S3 and KMS actions it needs.
- **No multi-tenant billing or user management** (noted as a non-goal in SPEC §2). Single-tenant
  deployment model.
- **Kubernetes secrets** for all credentials. Never in `values.yaml` or environment variables in
  source control.

## Audit trail

- **Correction log:** Every field correction records the original value, new value, who changed it,
  and when. This is both an audit trail and an accuracy measurement — it reveals which fields the
  extractor is bad at.
- **Delta Lake time travel:** Every Gold record references the Silver Delta version it derived from.
  Changes are versioned and reproducible.
- **Kafka events:** Document arrival events provide an immutable event log of all ingested documents.
- **OpenTelemetry traces:** End-to-end tracing across interactive and batch tiers. No content in
  attributes.

## Retention and deletion

### The genuine tension: time travel vs. right to be forgotten

Delta Lake's time-travel feature and data deletion requests are in direct conflict:

- **Time travel** allows reading historical versions of data, which is valuable for reproducibility
  and auditing.
- **Right to be forgotten** (GDPR Article 17, HIPAA individual access rights) requires that deleted
  data becomes unrecoverable.

**Resolution via VACUUM:**

- `VACUUM` removes data files older than the retention period (default: 7 days).
- After VACUUM runs, time-travel for versions older than the retention period is no longer possible.
- **Retention period bounds how long deleted data stays reachable.** Setting it to 7 days means that
  after a deletion, the data is physically unrecoverable within 7 days.

**Documented trade-off:**
- Shorter VACUUM retention = faster right-to-be-forgotten compliance, but reduced ability to
  reproduce historical results.
- Longer VACUUM retention = better reproducibility, but deleted data remains accessible longer.
- Current setting: **168 hours (7 days)**. This is a reasonable balance for most use cases.

### S3 object versioning

S3 bucket versioning is enabled for audit trail purposes. A lifecycle rule should be configured to
permanently delete old versions after the retention period (matching VACUUM retention) to ensure
deletion requests are honoured at the storage layer.

## Known gaps

| Gap | Risk | Mitigation path |
|---|---|---|
| No formal incident response plan | Delayed response to breaches | Document and test a plan |
| Single operator model | No separation of duties | Add RBAC when multi-tenant |
| No DLP scanning on uploads | Sensitive content in wrong schema | Add content classification |
| No audit log shipping | Logs could be tampered with | Ship to immutable storage (S3 + Glacier) |
| VACUUM retention is configurable | Could be set too long | Enforce via policy, alert on override |
| No automated compliance scanning | Drift from controls undetected | Add regular automated checks |
