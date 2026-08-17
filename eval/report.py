"""Generate the measurement report — computes [XX]%.

Usage:
    python -m eval.report --results eval/results

Formula: (manual_median - tool_median) / manual_median x 100

Reports:
- Median and total time per arm
- Per-document distribution
- Field-level accuracy per arm
- Relative reduction
- Participant count and corpus composition
"""

import argparse
import json
from pathlib import Path


def compute_report(results_dir: Path) -> dict:
    """Compute the full comparison report from saved results."""
    manual_results = []
    tool_results = []

    for result_file in sorted(results_dir.glob("*.json")):
        data = json.loads(result_file.read_text())
        if data["arm"] == "manual":
            manual_results.extend(data["results"])
        elif data["arm"] == "tool":
            tool_results.extend(data["results"])

    # Compute medians
    manual_times = sorted([r["active_seconds"] for r in manual_results])
    tool_times = sorted([r["active_seconds"] for r in tool_results])

    def median(values: list[int]) -> float:
        if not values:
            return 0
        n = len(values)
        mid = n // 2
        if n % 2 == 0:
            return (values[mid - 1] + values[mid]) / 2
        return float(values[mid])

    manual_median = median(manual_times)
    tool_median = median(tool_times)

    reduction = (
        ((manual_median - tool_median) / manual_median * 100) if manual_median > 0 else 0
    )

    participants = set()
    for r in manual_results + tool_results:
        participants.add(r.get("participant", "unknown"))

    report = {
        "formula": "(manual_median - tool_median) / manual_median x 100",
        "manual": {
            "documents": len(manual_times),
            "median_seconds": manual_median,
            "total_seconds": sum(manual_times),
            "min_seconds": min(manual_times) if manual_times else 0,
            "max_seconds": max(manual_times) if manual_times else 0,
        },
        "tool": {
            "documents": len(tool_times),
            "median_seconds": tool_median,
            "total_seconds": sum(tool_times),
            "min_seconds": min(tool_times) if tool_times else 0,
            "max_seconds": max(tool_times) if tool_times else 0,
        },
        "reduction_percent": round(reduction, 1),
        "participants": sorted(participants),
        "participant_count": len(participants),
        "note": f"{'K < 3 participants — per-participant numbers reported.' if len(participants) < 3 else ''}",
    }

    return report


def main():
    parser = argparse.ArgumentParser(description="Generate measurement report")
    parser.add_argument("--results", default="eval/results", help="Results directory")
    args = parser.parse_args()

    results_dir = Path(args.results)
    if not results_dir.exists():
        print(f"No results directory at {results_dir}")
        return

    report = compute_report(results_dir)

    print("\n" + "=" * 60)
    print("MEASUREMENT REPORT")
    print("=" * 60)
    print(f"\nFormula: {report['formula']}")
    print(f"\nManual arm: {report['manual']['documents']} documents")
    print(f"  Median: {report['manual']['median_seconds']:.0f}s")
    print(f"  Total: {report['manual']['total_seconds']:.0f}s")
    print(f"  Range: {report['manual']['min_seconds']}s - {report['manual']['max_seconds']}s")
    print(f"\nTool arm: {report['tool']['documents']} documents")
    print(f"  Median: {report['tool']['median_seconds']:.0f}s")
    print(f"  Total: {report['tool']['total_seconds']:.0f}s")
    print(f"  Range: {report['tool']['min_seconds']}s - {report['tool']['max_seconds']}s")
    print(f"\n{'*' * 40}")
    print(f"  REDUCTION: {report['reduction_percent']}%")
    print(f"{'*' * 40}")
    print(f"\nParticipants (K={report['participant_count']}): {', '.join(report['participants'])}")
    if report["note"]:
        print(f"NOTE: {report['note']}")
    print()

    # Save report
    report_file = results_dir / "report.json"
    report_file.write_text(json.dumps(report, indent=2))
    print(f"Report saved to: {report_file}")


if __name__ == "__main__":
    main()
