import math


def nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(percentile * len(ordered)) - 1]


def summarize(rows: list[dict]) -> dict:
    finished = [row for row in rows if row["status"] in ("success", "failure")]
    # The model can finish successfully while browser rendering or speech fails.
    # The exam counts that entire attempt as failed, while retaining any observed timings.
    successful = [
        row for row in finished
        if row["status"] == "success" and not row.get("browser_error")
    ]
    failure_count = len(finished) - len(successful)
    total = len(finished)
    model = [row["model_ttft_ms"] for row in successful if row["model_ttft_ms"] is not None]
    first = [row["first_char_ms"] for row in successful if row["first_char_ms"] is not None]
    speech = [row["speech_start_ms"] for row in successful if row["speech_start_ms"] is not None]
    first_questions = sum(row["turn"] == 1 for row in successful)
    follow_ups = sum(row["turn"] == 2 for row in successful)
    p50_model = nearest_rank(model, 0.5) if len(model) == len(successful) else None
    p95_model = nearest_rank(model, 0.95) if len(model) == len(successful) else None
    p95_first = nearest_rank(first, 0.95) if len(first) == len(successful) else None
    p95_speech = nearest_rank(speech, 0.95) if len(speech) == len(successful) else None
    complete = (
        total >= 20
        and total == len(rows)
        and first_questions > 0
        and follow_ups > 0
        and len(model) == len(successful)
        and len(first) == len(successful)
        and len(speech) == len(successful)
    )
    passed = (
        complete
        and total > 0
        and len(successful) / total >= 0.95
        and p50_model <= 2000
        and p95_model <= 5000
        and p95_first <= 6000
        and p95_speech <= 8000
    )
    return {
        "totalFinished": total,
        "success": len(successful),
        "failure": failure_count,
        "running": len(rows) - total,
        "successRate": round(len(successful) / total, 4) if total else None,
        "firstQuestions": first_questions,
        "followUps": follow_ups,
        "retries": sum(bool(row["is_retry"]) for row in rows),
        "modelSamples": len(model),
        "firstCharSamples": len(first),
        "speechSamples": len(speech),
        "modelP50Ms": p50_model,
        "modelP95Ms": p95_model,
        "firstCharP95Ms": p95_first,
        "speechStartP95Ms": p95_speech,
        "coldStart": [
            {"requestId": str(row["id"]), "status": row["status"], "modelTtftMs": row["model_ttft_ms"]}
            for row in rows if row["is_cold_start"]
        ],
        "evidenceComplete": complete,
        "thresholdsPassed": passed,
    }
