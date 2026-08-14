"""Delta Lake maintenance: OPTIMIZE, ZORDER, VACUUM.

A lakehouse with no compaction story becomes a small-file problem,
and knowing that is the point.

VACUUM retention bounds how long deleted data stays reachable.
This is documented in COMPLIANCE.md as the genuine tension between
time-travel and "right to be forgotten".
"""

from delta.tables import DeltaTable
from pyspark.sql import SparkSession

from batch.spark.config import get_delta_paths


def optimize_all_layers(
    spark: SparkSession,
    base_path: str | None = None,
    vacuum_hours: int = 168,  # 7 days
) -> dict[str, dict]:
    """Run OPTIMIZE and VACUUM on all Delta layers.

    OPTIMIZE/ZORDER on the columns actually filtered.
    VACUUM with a retention policy.

    Args:
        spark: Active Spark session
        base_path: Override base path
        vacuum_hours: Retention period for VACUUM (default 7 days)

    Returns:
        Metrics per layer
    """
    paths = get_delta_paths(base_path) if base_path else get_delta_paths()
    results = {}

    layer_configs = {
        "bronze": {
            "path": paths["bronze"],
            "zorder_cols": ["ingest_date", "content_hash"],
        },
        "silver": {
            "path": paths["silver"],
            "zorder_cols": ["document_id", "schema_name"],
        },
        "gold": {
            "path": paths["gold"],
            "zorder_cols": ["schema_name", "document_id"],
        },
    }

    for layer_name, config in layer_configs.items():
        try:
            table = DeltaTable.forPath(spark, config["path"])

            # Get pre-optimization file count
            pre_files = table.detail().select("numFiles").collect()[0][0]

            # OPTIMIZE with ZORDER
            zorder_clause = ", ".join(config["zorder_cols"])
            spark.sql(f"""
                OPTIMIZE delta.`{config['path']}`
                ZORDER BY ({zorder_clause})
            """)

            # VACUUM
            spark.sql(f"VACUUM delta.`{config['path']}` RETAIN {vacuum_hours} HOURS")

            # Get post-optimization file count
            post_files = table.detail().select("numFiles").collect()[0][0]

            # Get history info
            history = table.history(1).collect()[0]

            results[layer_name] = {
                "status": "success",
                "pre_files": pre_files,
                "post_files": post_files,
                "files_removed": pre_files - post_files if pre_files > post_files else 0,
                "vacuum_retention_hours": vacuum_hours,
                "latest_version": history["version"],
            }

        except Exception as e:
            results[layer_name] = {
                "status": "skipped",
                "reason": str(e),
            }

    return results


def get_time_travel_version(
    spark: SparkSession,
    layer: str,
    base_path: str | None = None,
) -> int:
    """Get the current Delta version for time-travel provenance.

    Every Gold record should record which Silver version it came from,
    so a result can be reproduced exactly after a re-extraction.
    """
    paths = get_delta_paths(base_path) if base_path else get_delta_paths()
    path = paths.get(layer)
    if not path:
        raise ValueError(f"Unknown layer: {layer}")

    table = DeltaTable.forPath(spark, path)
    return table.history(1).select("version").collect()[0]["version"]


def read_at_version(
    spark: SparkSession,
    layer: str,
    version: int,
    base_path: str | None = None,
):
    """Read a Delta table at a specific version (time travel).

    Used for reproducibility: given a Gold record's silver_delta_version,
    read Silver at that exact state to verify or reproduce the result.
    """
    paths = get_delta_paths(base_path) if base_path else get_delta_paths()
    path = paths.get(layer)
    if not path:
        raise ValueError(f"Unknown layer: {layer}")

    return spark.read.format("delta").option("versionAsOf", version).load(path)
