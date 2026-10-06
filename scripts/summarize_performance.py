"""Recompute performance results from an unfiltered application CSV export."""
import argparse
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.metrics import summarize


def read_records(path):
    with open(path, encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        required = {
            "id", "turn", "status", "is_cold_start", "is_retry",
            "model_ttft_ms", "first_char_ms", "speech_start_ms",
        }
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("CSV is missing required request columns")
        rows = []
        seen = set()
        for line, row in enumerate(reader, start=2):
            if not row["id"] or row["id"] in seen:
                raise ValueError(f"Line {line}: empty or duplicate request ID")
            seen.add(row["id"])
            if row["status"] not in ("success", "failure", "running"):
                raise ValueError(f"Line {line}: unknown status")
            row["turn"] = int(row["turn"])
            if row["turn"] not in (1, 2, 3):
                raise ValueError(f"Line {line}: invalid round")
            for key in ("model_ttft_ms", "first_char_ms", "speech_start_ms"):
                row[key] = float(row[key]) if row[key] else None
                if row[key] is not None and (not math.isfinite(row[key]) or row[key] < 0):
                    raise ValueError(f"Line {line}: invalid latency")
            for key in ("is_cold_start", "is_retry"):
                if row[key].lower() not in ("true", "false"):
                    raise ValueError(f"Line {line}: invalid boolean")
                row[key] = row[key].lower() == "true"
            rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file")
    args = parser.parse_args()
    try:
        report = summarize(read_records(args.csv_file))
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Invalid evidence file: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["thresholdsPassed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
