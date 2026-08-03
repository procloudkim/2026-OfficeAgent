"""Load immutable gold inputs and bind human approval to exact bytes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

Split = Literal["dev", "holdout"]
ApprovalStatus = Literal["pending", "approved"]


class DatasetValidationError(ValueError):
    """Raised when a gold-set file violates its integrity contract."""


@dataclass(frozen=True, slots=True)
class GoldDocument:
    """One corpus document owned by exactly one evaluation split."""

    split: Split
    path: Path
    relative_path: str
    filename: str
    content_type: str
    sha256: str


@dataclass(frozen=True, slots=True)
class GoldCase:
    """One answerable or topic-adjacent retrieval-negative question."""

    id: str
    split: Split
    question: str
    answerable: bool
    expected_sources: tuple[str, ...]
    answer_claims: tuple[tuple[str, ...], ...]
    evidence_claims: tuple[tuple[str, ...], ...]

    @property
    def case_type(self) -> str:
        """Expose the honest dataset label used in reviewer-facing JSONL."""

        return "answerable" if self.answerable else "retrieval_negative"


@dataclass(frozen=True, slots=True)
class QualityThresholds:
    """Explicit gates; every value is a closed-interval ratio."""

    retrieval_hit_rate: float
    mean_reciprocal_rank: float
    answer_claim_coverage: float
    citation_claim_coverage: float
    abstention_accuracy: float


@dataclass(frozen=True, slots=True)
class GoldDataset:
    """Integrity-checked view of one split in a versioned dataset."""

    root: Path
    version: str
    split: Split
    manifest_sha256: str
    dataset_sha256: str
    approval_status: ApprovalStatus
    documents: tuple[GoldDocument, ...]
    cases: tuple[GoldCase, ...]
    thresholds: QualityThresholds


def load_gold_dataset(
    root: Path,
    *,
    split: Split,
    require_approved: bool = False,
) -> GoldDataset:
    """Validate hashes, approval, split ownership, and case schema before use."""

    dataset_root = root.resolve()
    manifest_path = dataset_root / "manifest.json"
    approval_path = dataset_root / "approval.json"
    manifest_bytes = _read_bytes(manifest_path, "manifest")
    manifest = _json_object(manifest_bytes, "manifest.json")
    approval = _json_object(_read_bytes(approval_path, "approval"), "approval.json")

    _expect_schema_version(manifest, "manifest.json")
    _expect_schema_version(approval, "approval.json")
    version = _required_string(manifest, "dataset_version", "manifest.json")
    if _required_string(approval, "dataset_version", "approval.json") != version:
        raise DatasetValidationError("approval dataset_version does not match manifest")

    cases_config = _required_object(manifest, "cases", "manifest.json")
    cases_path = _safe_file(
        dataset_root,
        _required_string(cases_config, "path", "manifest cases"),
        "cases",
    )
    cases_bytes = _read_bytes(cases_path, "cases")
    declared_cases_sha = _sha256_string(cases_config, "sha256", "manifest cases")
    actual_cases_sha = _sha256(cases_bytes)
    if actual_cases_sha != declared_cases_sha:
        raise DatasetValidationError(
            f"cases SHA-256 mismatch: expected {declared_cases_sha}, got {actual_cases_sha}"
        )

    documents = _load_documents(dataset_root, manifest)
    cases = _load_cases(cases_bytes)
    _validate_case_sources(cases, documents)
    thresholds = _load_thresholds(manifest, split)

    manifest_sha = _sha256(manifest_bytes)
    dataset_sha = _dataset_sha256(
        manifest_sha256=manifest_sha,
        cases_sha256=actual_cases_sha,
        documents=documents,
    )
    approval_status = _validate_approval(
        approval,
        dataset_sha256=dataset_sha,
        require_approved=require_approved,
    )

    selected_documents = tuple(document for document in documents if document.split == split)
    selected_cases = tuple(case for case in cases if case.split == split)
    if not selected_documents:
        raise DatasetValidationError(f"split {split!r} has no documents")
    if not selected_cases:
        raise DatasetValidationError(f"split {split!r} has no cases")
    return GoldDataset(
        root=dataset_root,
        version=version,
        split=split,
        manifest_sha256=manifest_sha,
        dataset_sha256=dataset_sha,
        approval_status=approval_status,
        documents=selected_documents,
        cases=selected_cases,
        thresholds=thresholds,
    )


def _load_documents(root: Path, manifest: dict[str, Any]) -> tuple[GoldDocument, ...]:
    raw_documents = manifest.get("documents")
    if not isinstance(raw_documents, list) or not raw_documents:
        raise DatasetValidationError("manifest documents must be a non-empty array")
    documents: list[GoldDocument] = []
    seen_filenames: set[str] = set()
    for index, value in enumerate(raw_documents):
        where = f"manifest documents[{index}]"
        if not isinstance(value, dict):
            raise DatasetValidationError(f"{where} must be an object")
        split = _split(value.get("split"), where)
        filename = _required_string(value, "filename", where)
        if Path(filename).name != filename:
            raise DatasetValidationError(f"{where} filename must not contain a path")
        if filename in seen_filenames:
            raise DatasetValidationError(f"duplicate document filename: {filename}")
        seen_filenames.add(filename)
        path = _safe_file(root, _required_string(value, "path", where), where)
        expected_sha = _sha256_string(value, "sha256", where)
        actual_sha = _sha256(_read_bytes(path, where))
        if actual_sha != expected_sha:
            raise DatasetValidationError(
                f"document SHA-256 mismatch for {path.relative_to(root)}: "
                f"expected {expected_sha}, got {actual_sha}"
            )
        documents.append(
            GoldDocument(
                split=split,
                path=path,
                relative_path=path.relative_to(root).as_posix(),
                filename=filename,
                content_type=_required_string(value, "content_type", where),
                sha256=actual_sha,
            )
        )
    return tuple(documents)


def _load_cases(cases_bytes: bytes) -> tuple[GoldCase, ...]:
    cases: list[GoldCase] = []
    seen_ids: set[str] = set()
    try:
        text = cases_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DatasetValidationError("cases.jsonl must be UTF-8") from exc
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        where = f"cases.jsonl line {line_number}"
        value = _json_object(line.encode(), where)
        case_id = _required_string(value, "id", where)
        if case_id in seen_ids:
            raise DatasetValidationError(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)
        answerable = value.get("answerable")
        if not isinstance(answerable, bool):
            raise DatasetValidationError(f"{where} answerable must be boolean")
        expected_case_type = "answerable" if answerable else "retrieval_negative"
        if value.get("case_type") != expected_case_type:
            raise DatasetValidationError(f"{where} case_type must be {expected_case_type}")
        expected_sources = _string_tuple(value.get("expected_sources"), where)
        answer_claims = _claim_groups(value.get("answer_claims"), where)
        evidence_claims = _claim_groups(value.get("evidence_claims"), where)
        if answerable and not (expected_sources and answer_claims and evidence_claims):
            raise DatasetValidationError(
                f"{where} answerable case requires sources and claim groups"
            )
        if not answerable and (expected_sources or answer_claims or evidence_claims):
            raise DatasetValidationError(
                f"{where} retrieval-negative must not declare expected evidence"
            )
        cases.append(
            GoldCase(
                id=case_id,
                split=_split(value.get("split"), where),
                question=_required_string(value, "question", where),
                answerable=answerable,
                expected_sources=expected_sources,
                answer_claims=answer_claims,
                evidence_claims=evidence_claims,
            )
        )
    if not cases:
        raise DatasetValidationError("cases.jsonl must contain at least one case")
    return tuple(cases)


def _validate_case_sources(
    cases: tuple[GoldCase, ...],
    documents: tuple[GoldDocument, ...],
) -> None:
    split_by_filename = {document.filename: document.split for document in documents}
    for case in cases:
        for filename in case.expected_sources:
            source_split = split_by_filename.get(filename)
            if source_split is None:
                raise DatasetValidationError(
                    f"case {case.id} references unknown document {filename}"
                )
            if source_split != case.split:
                raise DatasetValidationError(f"case {case.id} must reference a same-split document")


def _load_thresholds(manifest: dict[str, Any], split: Split) -> QualityThresholds:
    thresholds = _required_object(manifest, "thresholds", "manifest.json")
    values = _required_object(thresholds, split, "manifest thresholds")

    def ratio(key: str) -> float:
        value = values.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DatasetValidationError(f"threshold {split}.{key} must be numeric")
        result = float(value)
        if not 0.0 <= result <= 1.0:
            raise DatasetValidationError(f"threshold {split}.{key} must be in [0, 1]")
        return result

    return QualityThresholds(
        retrieval_hit_rate=ratio("retrieval_hit_rate"),
        mean_reciprocal_rank=ratio("mean_reciprocal_rank"),
        answer_claim_coverage=ratio("answer_claim_coverage"),
        citation_claim_coverage=ratio("citation_claim_coverage"),
        abstention_accuracy=ratio("abstention_accuracy"),
    )


def _validate_approval(
    approval: dict[str, Any],
    *,
    dataset_sha256: str,
    require_approved: bool,
) -> ApprovalStatus:
    status = approval.get("status")
    if status not in ("pending", "approved"):
        raise DatasetValidationError("approval status must be pending or approved")
    approved_hash = approval.get("approved_dataset_sha256")
    approved_by = approval.get("approved_by")
    approved_at = approval.get("approved_at")
    if status == "pending":
        if any(value is not None for value in (approved_hash, approved_by, approved_at)):
            raise DatasetValidationError("pending approval fields must be null")
        if require_approved:
            raise DatasetValidationError("gold dataset is not approved")
        return "pending"
    if approved_hash != dataset_sha256:
        raise DatasetValidationError(
            f"approval hash does not match dataset: expected {dataset_sha256}"
        )
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise DatasetValidationError("approved_by must identify the dataset owner")
    if not isinstance(approved_at, str) or not approved_at.strip():
        raise DatasetValidationError("approved_at must be set")
    try:
        parsed_approval_time = datetime.fromisoformat(
            approved_at.removesuffix("Z") + ("+00:00" if approved_at.endswith("Z") else "")
        )
    except ValueError:
        raise DatasetValidationError(
            "approved_at must be an ISO-8601 timestamp with timezone"
        ) from None
    if parsed_approval_time.tzinfo is None or parsed_approval_time.utcoffset() is None:
        raise DatasetValidationError(
            "approved_at must be an ISO-8601 timestamp with timezone"
        )
    return "approved"


def _dataset_sha256(
    *,
    manifest_sha256: str,
    cases_sha256: str,
    documents: tuple[GoldDocument, ...],
) -> str:
    payload = {
        "format": "officeagent-gold-dataset-v1",
        "manifest_sha256": manifest_sha256,
        "cases_sha256": cases_sha256,
        "documents": [
            {"path": document.relative_path, "sha256": document.sha256}
            for document in sorted(documents, key=lambda item: item.relative_path)
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return _sha256(encoded)


def _safe_file(root: Path, relative: str, label: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise DatasetValidationError(f"{label} path escapes dataset root") from exc
    if not candidate.is_file():
        raise DatasetValidationError(f"{label} file does not exist: {relative}")
    return candidate


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise DatasetValidationError(f"cannot read {label}: {path}") from exc


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetValidationError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise DatasetValidationError(f"{label} must contain a JSON object")
    return value


def _expect_schema_version(value: dict[str, Any], label: str) -> None:
    if value.get("schema_version") != 1:
        raise DatasetValidationError(f"{label} schema_version must be 1")


def _required_object(value: dict[str, Any], key: str, label: str) -> dict[str, Any]:
    result = value.get(key)
    if not isinstance(result, dict):
        raise DatasetValidationError(f"{label} {key} must be an object")
    return result


def _required_string(value: dict[str, Any], key: str, label: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise DatasetValidationError(f"{label} {key} must be a non-empty string")
    return result.strip()


def _sha256_string(value: dict[str, Any], key: str, label: str) -> str:
    result = _required_string(value, key, label).lower()
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise DatasetValidationError(f"{label} {key} must be a SHA-256 hex digest")
    return result


def _split(value: object, label: str) -> Split:
    if value not in ("dev", "holdout"):
        raise DatasetValidationError(f"{label} split must be dev or holdout")
    return value


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise DatasetValidationError(f"{label} expected_sources must be a string array")
    return tuple(item.strip() for item in value)


def _claim_groups(value: object, label: str) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list):
        raise DatasetValidationError(f"{label} claim groups must be an array")
    groups: list[tuple[str, ...]] = []
    for group in value:
        if (
            not isinstance(group, list)
            or not group
            or any(not isinstance(alias, str) or not alias.strip() for alias in group)
        ):
            raise DatasetValidationError(
                f"{label} each claim group must contain non-empty string aliases"
            )
        groups.append(tuple(alias.strip() for alias in group))
    return tuple(groups)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
