"""
QA gate: run Layer 1 -> 2 -> 3, cheapest first.

Two modes (config.QA_CALIBRATION):
  - CALIBRATION (week 1): run ALL layers, LOG every score, NEVER reject. Use the
    logs to set real thresholds, then flip QA_CALIBRATION = False.
  - ENFORCE: stop at the first failing layer; that ad fails QA.

Every run appends one JSON line to logs/qa_scores.jsonl.
"""
from __future__ import annotations

import json
import time

import config
from schema import QAResult
from qa import layer1_technical, layer2_transcript, layer3_vision

_LAYERS = [
    (1, layer1_technical),
    (2, layer2_transcript),
    (3, layer3_vision),
]


def run_qa(bundle, mp4_path: str) -> QAResult:
    all_scores: dict = {}
    all_failures: list[str] = []
    layer_reached = 0
    enforced_pass = True

    for num, mod in _LAYERS:
        layer_reached = num
        failures, scores = mod.run(bundle, mp4_path)
        all_scores[f"layer{num}"] = scores
        if failures:
            all_failures += [f"L{num}: {f}" for f in failures]
            if not config.QA_CALIBRATION:
                enforced_pass = False
                break  # enforce mode: stop at first failing layer

    passed = True if config.QA_CALIBRATION else enforced_pass
    result = QAResult(
        passed=passed,
        layer_reached=layer_reached,
        scores=all_scores,
        failures=all_failures,
        calibration=config.QA_CALIBRATION,
    )
    _log(bundle, result)
    return result


def _log(bundle, result: QAResult) -> None:
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ad_id": bundle.ad_id,
        "ad_type": bundle.ad_type,
        "mode": "calibration" if result.calibration else "enforce",
        "passed": result.passed,
        "layer_reached": result.layer_reached,
        "failures": result.failures,
        "scores": result.scores,
    }
    with open(config.QA_LOG, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")
