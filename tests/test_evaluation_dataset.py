"""Versioned gold-set validation and approval semantics."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from officeagent.evaluation.dataset import DatasetValidationError, load_gold_dataset

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLD_V1 = PROJECT_ROOT / "evals" / "gold" / "v1"


def test_committed_gold_set_is_owner_approved_at_the_exact_dataset_hash() -> None:
    dataset = load_gold_dataset(GOLD_V1, split="dev")
    holdout = load_gold_dataset(GOLD_V1, split="holdout")

    assert dataset.version == "v1"
    assert dataset.split == "dev"
    assert dataset.approval_status == "approved"
    assert len(dataset.cases) == 15
    assert sum(case.answerable for case in dataset.cases) == 10
    assert sum(not case.answerable for case in dataset.cases) == 5
    assert sum(case.answerable for case in holdout.cases) == 10
    assert sum(not case.answerable for case in holdout.cases) == 5
    assert dataset.dataset_sha256 == holdout.dataset_sha256
    assert {document.split for document in dataset.documents} == {"dev"}
    assert all(document.path.is_file() for document in dataset.documents)

    approved = load_gold_dataset(GOLD_V1, split="dev", require_approved=True)
    assert approved.dataset_sha256 == dataset.dataset_sha256


def test_dev_gold_corpus_exactly_matches_the_repository_samples() -> None:
    manifest = json.loads((GOLD_V1 / "manifest.json").read_text(encoding="utf-8"))
    dev_documents = {
        item["filename"]: GOLD_V1 / item["path"]
        for item in manifest["documents"]
        if item["split"] == "dev"
    }

    assert set(dev_documents) == {
        "dev-company-policy.txt",
        "dev-development-guide.md",
    }
    assert dev_documents["dev-company-policy.txt"].read_bytes() == (
        PROJECT_ROOT / "sample-docs" / "company-policy.txt"
    ).read_bytes()
    assert dev_documents["dev-development-guide.md"].read_bytes() == (
        PROJECT_ROOT / "sample-docs" / "development-guide.md"
    ).read_bytes()


def test_dev_negatives_are_the_five_absent_policy_details() -> None:
    dataset = load_gold_dataset(GOLD_V1, split="dev")

    assert {case.id for case in dataset.cases if not case.answerable} == {
        "dev-11-education-preapproval",
        "dev-12-remote-fixed-days",
        "dev-13-remote-core-hours",
        "dev-14-release-schedule",
        "dev-15-rollback-rule",
    }


def test_loader_rejects_a_cases_file_changed_after_manifest_hashing(tmp_path: Path) -> None:
    root = _write_minimal_dataset(tmp_path)
    cases_path = root / "cases.jsonl"
    cases_path.write_text(cases_path.read_text() + "\n", encoding="utf-8")

    with pytest.raises(DatasetValidationError, match="cases SHA-256 mismatch"):
        load_gold_dataset(root, split="dev")


def test_approved_dataset_hash_binds_manifest_cases_and_all_documents(
    tmp_path: Path,
) -> None:
    root = _write_minimal_dataset(tmp_path)
    pending = load_gold_dataset(root, split="dev")
    approval = {
        "schema_version": 1,
        "dataset_version": "v1",
        "status": "approved",
        "approved_dataset_sha256": pending.dataset_sha256,
        "approved_by": "dataset-owner",
        "approved_at": "2026-08-03T00:00:00Z",
    }
    (root / "approval.json").write_text(
        json.dumps(approval, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    dataset = load_gold_dataset(root, split="dev", require_approved=True)
    assert dataset.approval_status == "approved"

    manifest_path = root / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    manifest["description"] = "changed after approval"
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="approval hash"):
        load_gold_dataset(root, split="dev", require_approved=True)

    manifest_path.write_bytes(manifest_bytes)
    document = root / "documents" / "dev" / "policy.txt"
    document.write_text("The corpus changed after approval.", encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="document SHA-256 mismatch"):
        load_gold_dataset(root, split="dev", require_approved=True)


@pytest.mark.parametrize(
    "approved_at",
    ["not-a-time", "2026-08-03", "2026-08-03T00:00:00"],
)
def test_approved_dataset_requires_an_iso8601_timestamp_with_timezone(
    tmp_path: Path,
    approved_at: str,
) -> None:
    root = _write_minimal_dataset(tmp_path)
    pending = load_gold_dataset(root, split="dev")
    approval = {
        "schema_version": 1,
        "dataset_version": "v1",
        "status": "approved",
        "approved_dataset_sha256": pending.dataset_sha256,
        "approved_by": "dataset-owner",
        "approved_at": approved_at,
    }
    (root / "approval.json").write_text(
        json.dumps(approval, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(DatasetValidationError, match="ISO-8601 timestamp with timezone"):
        load_gold_dataset(root, split="dev", require_approved=True)


def test_case_cannot_reference_a_document_from_the_other_split(tmp_path: Path) -> None:
    root = _write_minimal_dataset(tmp_path)
    cases_path = root / "cases.jsonl"
    case = json.loads(cases_path.read_text(encoding="utf-8"))
    case["expected_sources"] = ["holdout-policy.txt"]
    cases_bytes = (json.dumps(case, ensure_ascii=False) + "\n").encode()
    cases_path.write_bytes(cases_bytes)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["cases"]["sha256"] = hashlib.sha256(cases_bytes).hexdigest()
    holdout = root / "documents" / "holdout" / "policy.txt"
    holdout.parent.mkdir(parents=True)
    holdout.write_text("Holdout-only policy.", encoding="utf-8")
    manifest["documents"].append(
        {
            "split": "holdout",
            "path": "documents/holdout/policy.txt",
            "filename": "holdout-policy.txt",
            "content_type": "text/plain",
            "sha256": hashlib.sha256(holdout.read_bytes()).hexdigest(),
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(DatasetValidationError, match="same-split document"):
        load_gold_dataset(root, split="dev")


def test_dataset_hash_is_identical_across_clone_roots(tmp_path: Path) -> None:
    first_root = _write_minimal_dataset(tmp_path / "first")
    second_root = tmp_path / "second" / "v1"
    shutil.copytree(first_root, second_root)

    first = load_gold_dataset(first_root, split="dev")
    second = load_gold_dataset(second_root, split="dev")

    assert first.root != second.root
    assert first.dataset_sha256 == second.dataset_sha256


def test_answerable_evidence_aliases_exist_in_the_expected_corpus() -> None:
    manifest = json.loads((GOLD_V1 / "manifest.json").read_text(encoding="utf-8"))
    documents = {
        item["filename"]: (GOLD_V1 / item["path"]).read_text(encoding="utf-8").casefold()
        for item in manifest["documents"]
    }
    cases = [
        json.loads(line)
        for line in (GOLD_V1 / "cases.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    for case in cases:
        if not case["answerable"]:
            continue
        source_text = " ".join(documents[name] for name in case["expected_sources"])
        for aliases in case["evidence_claims"]:
            assert any(alias.casefold() in source_text for alias in aliases), (
                case["id"],
                aliases,
            )


def _write_minimal_dataset(tmp_path: Path) -> Path:
    root = tmp_path / "v1"
    document_dir = root / "documents" / "dev"
    document_dir.mkdir(parents=True)
    document = document_dir / "policy.txt"
    document.write_text("Annual education budget is 2,000,000 KRW.", encoding="utf-8")
    case = {
        "id": "dev-education-budget",
        "split": "dev",
        "case_type": "answerable",
        "question": "What is the annual education budget?",
        "answerable": True,
        "expected_sources": ["dev-policy.txt"],
        "answer_claims": [["2,000,000", "2000000"]],
        "evidence_claims": [["education budget"]],
    }
    cases_bytes = (json.dumps(case, ensure_ascii=False) + "\n").encode()
    (root / "cases.jsonl").write_bytes(cases_bytes)
    manifest = {
        "schema_version": 1,
        "dataset_version": "v1",
        "description": "fixture",
        "cases": {
            "path": "cases.jsonl",
            "sha256": hashlib.sha256(cases_bytes).hexdigest(),
        },
        "documents": [
            {
                "split": "dev",
                "path": "documents/dev/policy.txt",
                "filename": "dev-policy.txt",
                "content_type": "text/plain",
                "sha256": hashlib.sha256(document.read_bytes()).hexdigest(),
            }
        ],
        "thresholds": {
            "dev": {
                "retrieval_hit_rate": 1.0,
                "mean_reciprocal_rank": 1.0,
                "answer_claim_coverage": 1.0,
                "citation_claim_coverage": 1.0,
                "abstention_accuracy": 1.0,
            }
        },
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    approval = {
        "schema_version": 1,
        "dataset_version": "v1",
        "status": "pending",
        "approved_dataset_sha256": None,
        "approved_by": None,
        "approved_at": None,
    }
    (root / "approval.json").write_text(
        json.dumps(approval, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return root
