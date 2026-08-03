# Gold set v1

This proposed dataset contains 30 cases: 15 `dev` and 15 `holdout`. Each split has
10 answerable questions and five topic-adjacent `retrieval_negative` questions. A
negative shares vocabulary with its split corpus but asks for a fact the corpus does
not state. It is deliberately not an unrelated-query shortcut.

`holdout` means process holdout only: run it after dev and do not tune against it within
the same iteration. Its files are committed for reviewer inspection and the repository
cannot enforce non-inspection, so this is not a blind benchmark. Run each split in a separate process. For every run,
the CLI creates a randomly named, split-labelled database on loopback PostgreSQL,
checks that the migrated corpus is empty, and drops the database even after failure.
It rejects remote, non-`postgres` admin, normal product, and reused database targets.

## Integrity and adoption

`manifest.json` pins `cases.jsonl` and every corpus document by SHA-256. The loader
also computes a portable dataset SHA-256 over the exact manifest bytes, cases digest,
and dataset-relative document paths and digests. `approval.json` is intentionally
`pending`: no user adoption is claimed by this change.

Review the cases and source documents before adoption. Then obtain the proposed hash:

```bash
uv run python -m officeagent.evaluation --split dev --verify-only
```

Only the dataset owner should change `approval.json` to `approved`, copy that exact
`dataset_sha256` into `approved_dataset_sha256`, identify `approved_by`, and record an
ISO-8601 `approved_at` with timezone. Before doing so, confirm each answerability label,
expected source, claim alias and predeclared threshold. `--require-approved` rejects
pending, malformed approval or byte-drifted data. Pending results may diagnose the
harness but must not gate product defaults or release quality.

## Metrics

The evaluator reports retrieval hit rate, mean reciprocal rank, answer claim coverage,
citation claim coverage, and exact abstention accuracy. Claim coverage is a transparent
Unicode-normalized lexical-alias check. It is not semantic entailment, NLI, or factuality
proof. The product's standard no-evidence answer, `grounded=false`, and zero citations
are all required for a retrieval-negative to pass.

For an answerable case, retrieval hit means any expected filename appears in server
citations and reciprocal rank uses the first such citation. A claim group passes when
at least one declared alias is present after Unicode, case, punctuation and numeric
separator normalization. Answer coverage checks the generated text; citation coverage
checks an expected-source quote. Each command evaluates all 15 cases in manifest order.

Thresholds are declared in the manifest rather than adjusted after a run. In
particular, abstention accuracy is 1.0; topic-adjacent negatives are not hidden by an
average. A failing report is a product signal, not a reason to relabel a case.

## Product-pipeline run

Point `OFFICEAGENT_DATABASE_URL` at the normal local development PostgreSQL connection
(or set `OFFICEAGENT_DATABASE_ADMIN_URL` to its local `postgres` database), ensure the
local Codex subscription is available, and run the pending dataset as a diagnostic:

```bash
uv run python -m officeagent.evaluation --split dev --output .data/evaluation/dev.json
```

The CLI uses that connection only to derive or validate a loopback `postgres` admin
target; it never selects the normal product database for evaluation writes. After the
dataset owner adopts the exact hash, add `--require-approved` for an official gate. The
admin role needs local `CREATEDB` and migration extension privileges. An abrupt process
or host termination can leave an `officeagent_eval_<split>_<12hex>` database; inspect
that exact prefix before any manual cleanup.

The run sends every selected synthetic question and the retrieved synthetic evidence
to the locally authenticated Codex App Server and therefore uses the user's Codex
subscription. Do not replace these fixtures with confidential documents. CI and this
implementation capsule exercise the loader and metrics directly and use fakes only at
the ingestion and Q&A boundaries. They do not execute this Codex path.

Live model behavior can vary outside the dataset hash, so keep the commit SHA and local
Codex version with any diagnostic report. The schema v1 output contains the synthetic
questions, generated answers, citation filenames and aggregate metrics; keep it under
ignored `.data/` and do not commit it as a stable benchmark receipt.

Repeat for `holdout`; the CLI provisions a different disposable database. Exit code 0
means all declared thresholds passed, 1 means a quality threshold failed, and 2 means
dataset or isolation validation failed. Exit code 3 is a sanitized product, DB, or Codex
runtime failure. `--report-only` changes only a threshold failure from 1 to 0; validation,
isolation, and runtime failures remain nonzero.
