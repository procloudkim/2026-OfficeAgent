"""Versioned, deterministic quality evaluation for the product pipeline."""

from officeagent.evaluation.dataset import load_gold_dataset
from officeagent.evaluation.runner import EvaluationRunner

__all__ = ["EvaluationRunner", "load_gold_dataset"]
