# Requirements Traceability

> 이 문서는 V1 `29a429f` rollback 기준선의 역사적 증거입니다. 현재 V2 상태는
> [V2 Requirements Traceability](./V2_REQUIREMENTS_TRACEABILITY.md)를 따릅니다.

PRD 요구를 구현·테스트·실런타임 증거에 연결한 평가자용 표입니다.

아래 cache 동작의 구현 기준선은 source commit `0565fa5`이며, 바로 뒤
`10af4e7`은 Retrobot이 만든 회고 commit입니다. 2026-07-29에 같은 source tree의
offline suite 25개가 통과했습니다. 2026-07-30 검토 tree에서는 filename prompt
metadata와 Retrobot explicit-path commit 회귀 테스트 2개를 더해 전체 27개가
통과했습니다. 실제 Codex App
Server·Uvicorn SSE 실행은 이전 `e838cc7`의 역사적 영수증이며 이후 source에서
재실행하지 않았습니다.

| 요구 | 구현 | 검증 | 상태 |
|---|---|---|---|
| PDF/TXT 2종 이상 추출 | `infrastructure/parsers.py` (PDF/TXT/MD) | `test_ingestion.py`, `test_parsers_and_chunking.py` | VERIFIED |
| 검색 가능 저장 | `application/ingestion.py`, `infrastructure/chroma_store.py` | 실제 Chroma를 쓰는 `test_ingestion.py` | VERIFIED |
| RAG 검색·답변 | `application/question_answering.py` | `test_retrieval.py`, `e838cc7` historic live smoke | VERIFIED |
| Codex SDK/CLI 호출 | `infrastructure/codex_app_server.py` | protocol fake 5경로 + `e838cc7` historic live smoke | VERIFIED |
| 문서/부분 검색 provenance | `Chunk` → deterministic `Citation` | retrieval·SSE 테스트; answer-level entailment는 미검증 | VERIFIED |
| 근거 없음/환각 억제 | score gate, `NO_EVIDENCE_ANSWER`, LLM skip | `test_retrieval.py` 호출 수 검증 | VERIFIED |
| 실제 스트리밍 | App Server delta → SSE delta | protocol·SSE 테스트, `e838cc7` Uvicorn live 17 delta | VERIFIED |
| 동일 질문 cache | normalized exact SQLite lookup | cache·SSE 테스트 | VERIFIED |
| 유사 질문 cache | cosine `>=0.95` lookup | `test_cache_invalidation.py` | VERIFIED |
| cache hit 노출 | `cache_hit`, `cache_match` | SSE·cache 테스트 | VERIFIED |
| 문서 변경 무효화 | `CREATED`/`UNCHANGED` corpus 전체 + `UPDATED` dependency 무효화 | 실제 SQLite/Chroma-backed 흐름의 embedding·vector 경계 failure/retry 테스트 | VERIFIED |
| prompt 직접 설계 | `application/prompts.py` | `PROMPT_DESIGN.md`, retrieval·metadata boundary 테스트 | VERIFIED |
| 한 줄 실행 | `uv run officeagent` | `e838cc7` 실제 기동 + `/health/live` 200 | VERIFIED |
| 한 줄 테스트 | `uv run pytest` | `e838cc7` 20개; `0565fa5` 25개; 2026-07-30 검토 tree 27개 PASS | VERIFIED |
| 필수 3 테스트 경로 | ingestion/retrieval/cache invalidation | 동명 test 파일 포함 2026-07-30 검토 tree 27개 PASS | VERIFIED |
| 문서–코드 일치 | README/Architecture/ADR/traceability | diff·tree 대조 | REVIEWED |

## 불합격 방지 점검

| Gate | 근거 | 판정 |
|---|---|---|
| 실행 방법 존재 | `pyproject.toml` console script + README | PASS |
| 하드코딩 응답이 아닌 실제 LLM | subprocess App Server JSON-RPC + live smoke | PASS |
| 실제 vector DB | Chroma PersistentClient adapter/test | PASS |
| 실제 cache DB | SQLite schema/query/invalidation test | PASS |
| 자동화 테스트 존재 | 핵심 3경로 포함 전체 suite | PASS |
| 오픈소스 통째 복사 아님 | 템플릿 health skeleton 위 과제 전용 설계·구현 | REVIEWED |
| 허위 문서 기재 없음 | 현재 한계와 test/live 증거 분리 | REVIEWED |
| 168시간 내 산출물 | Git committer date/remote push로 최종 판정 | PROCESS |

상태 의미: `VERIFIED`는 실행 증거 있음, `REVIEWED`는 정적 대조,
`PROCESS`는 Git/GitHub 기록으로 판정합니다. 역사적 실행 증거는 검증 열에
해당 commit을 명시해 같은 source tree의 offline 검증과 구분합니다.

## 현재 증거 경계

Cache remediation source baseline은 `0565fa5`이고 `10af4e7`은 그 직후의
Retrobot 회고 commit입니다.
`.venv/Scripts/python.exe -m pytest tests -p no:cacheprovider -q`로 신규 문서
metadata 선반영, 실패 창의 old-corpus recache, same-byte `UNCHANGED` retry,
corpus-wide invalidation과 새 문서 citation을 검증합니다. 정상 same-hash
재업로드의 보수적 전체 무효화 정책도 별도 회귀 테스트로 고정했습니다.

| 동작 | source-baseline 테스트 (`tests/test_cache_invalidation.py`) | 증거 상태 |
|---|---|---|
| UPDATE embedding 실패·재시도 | `test_embedding_failure_invalidates_related_cache_before_retry` | PASS — 2026-07-29 offline suite |
| UPDATE vector 실패·재시도 | `test_vector_failure_invalidates_related_cache_before_retry` | PASS — 2026-07-29 offline suite |
| CREATED embedding 실패·재시도·failure-window recache | `test_created_embedding_failure_retry_invalidates_failure_window_recache` | PASS — 2026-07-29 offline suite |
| CREATED vector 실패·재시도·failure-window recache | `test_created_vector_failure_retry_invalidates_failure_window_recache` | PASS — 2026-07-29 offline suite |
| 정상 same-hash corpus-wide 정책 | `test_normal_same_hash_reupload_invalidates_corpus_cache` | PASS — 2026-07-29 offline suite |
| 정상 UPDATE related/unrelated 정책 | `test_document_update_invalidates_related_cache_only` | PASS — 2026-07-29 offline suite |

[`docs/verification/2026-07-28.md`](verification/2026-07-28.md)는
`e838cc7` 당시 20-test baseline과 실제 Codex/Uvicorn SSE의 역사적 영수증입니다.
같은 source tree의 offline 검증을 대체하지 않으며, 반대로 그 offline
suite도 fresh Codex/SSE 실행을 증명하지 않습니다.
## 2026-07-30 검토 tree 영수증

- `.venv/Scripts/python.exe -m pytest tests -p no:cacheprovider -q` → `27 passed`
- `.venv/Scripts/ruff.exe check --no-cache .` → `All checks passed!`
- workspace-local 임시 `UV_CACHE_DIR`에서 `uv lock --check` → 100 packages resolved,
  lock 변경 없음
- `tests/test_prompts.py`는 client-controlled filename이 evidence attribute를 닫지
  못하도록 metadata escape를 검증합니다.
- `tests/test_retrobot_hook.py`는 자동 회고가 생성 회고·timeline 두 path만
  stage·commit하고 `retros/` 전체를 수집하지 않는 구조 계약을 검증합니다.
- 실제 일반 commit `251bf3c` 뒤 자동 `0548cec`에는 새 회고와 timeline만
  포함됐고 다른 dirty 변경은 남았습니다. push 뒤 origin/main과 local HEAD가
  `0548cec`로 일치했습니다.
- 이 27-test 영수증은 fresh FastEmbed download, Codex login/App Server,
  Uvicorn socket live SSE, 실제 한국어 검색 품질, citation entailment, 동시성,
  cross-store atomicity를 증명하지 않습니다.
