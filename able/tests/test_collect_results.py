from __future__ import annotations

import json

from able.evals.collect_results import summarize_corpus_progress


def test_summarize_corpus_progress_counts_pairs_and_threshold(tmp_path):
    first = tmp_path / "distillation_20260401_000000.jsonl"
    second = tmp_path / "distillation_20260401_010000.jsonl"

    with open(first, "w", encoding="utf-8") as handle:
        for idx in range(60):
            handle.write(json.dumps({"test": f"a-{idx}"}) + "\n")

    with open(second, "w", encoding="utf-8") as handle:
        for idx in range(45):
            handle.write(json.dumps({"test": f"b-{idx}"}) + "\n")

    summary = summarize_corpus_progress(tmp_path, threshold=100)

    assert summary["files"] == 2
    assert summary["total_pairs"] == 105
    assert summary["ready"] is True


def test_summarize_corpus_progress_handles_empty_directory(tmp_path):
    summary = summarize_corpus_progress(tmp_path, threshold=100)

    assert summary["files"] == 0
    assert summary["total_pairs"] == 0
    assert summary["ready"] is False
