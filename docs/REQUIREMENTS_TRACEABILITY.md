# Requirements Traceability

PRD 요구를 구현·테스트·실런타임 증거에 연결한 평가자용 표입니다.

| 요구 | 구현 | 검증 | 상태 |
|---|---|---|---|
| PDF/TXT 2종 이상 추출 | `infrastructure/parsers.py` (PDF/TXT/MD) | `test_ingestion.py`, `test_parsers_and_chunking.py` | VERIFIED |
| 검색 가능 저장 | `application/ingestion.py`, `infrastructure/chroma_store.py` | 실제 Chroma를 쓰는 `test_ingestion.py` | VERIFIED |
| RAG 검색·답변 | `application/question_answering.py` | `test_retrieval.py`, live smoke | VERIFIED |
| Codex SDK/CLI 호출 | `infrastructure/codex_app_server.py` | protocol fake 5경로 + live smoke | VERIFIED |
| 문서/부분 출처 | `Chunk` → deterministic `Citation` | retrieval·SSE 테스트 | VERIFIED |
| 근거 없음/환각 억제 | score gate, `NO_EVIDENCE_ANSWER`, LLM skip | `test_retrieval.py` 호출 수 검증 | VERIFIED |
| 실제 스트리밍 | App Server delta → SSE delta | protocol·SSE 테스트, Uvicorn final live 17 delta | VERIFIED |
| 동일 질문 cache | normalized exact SQLite lookup | cache·SSE 테스트 | VERIFIED |
| 유사 질문 cache | cosine `>=0.95` lookup | `test_cache_invalidation.py` | VERIFIED |
| cache hit 노출 | `cache_hit`, `cache_match` | SSE·cache 테스트 | VERIFIED |
| 문서 변경 무효화 | `CREATED`/`UNCHANGED` corpus 전체 + `UPDATED` dependency 무효화 | 실제 SQLite/Chroma-backed 흐름의 embedding·vector 경계 failure/retry 테스트 | IMPLEMENTED |
| prompt 직접 설계 | `application/prompts.py` | `PROMPT_DESIGN.md`, retrieval prompt 검증 | VERIFIED |
| 한 줄 실행 | `uv run officeagent` | 실제 기동 + `/health/live` 200 | VERIFIED |
| 한 줄 테스트 | `uv run pytest` | 로컬 전체 suite | VERIFIED |
| 필수 3 테스트 경로 | ingestion/retrieval/cache invalidation | 동명 test 파일, 현재 working tree 25개 offline 테스트 | IMPLEMENTED |
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

상태 의미: `VERIFIED`는 실행 증거 있음, `IMPLEMENTED`는 코드 존재하나 표의 특정
런타임 영수증 갱신 전, `REVIEWED`는 정적 대조, `PROCESS`는 Git/GitHub 기록으로
판정합니다.

## 현재 working-tree 증거 경계

2026-07-29의 미커밋 working tree에서는 embedding 실패와 vector 실패 각각에
대해 신규 문서 metadata 선반영, 실패 창의 old-corpus recache, same-byte
`UNCHANGED` retry, corpus-wide invalidation, 새 문서 citation을 한 흐름으로
검증합니다. 정상 same-hash 재업로드의 보수적 전체 무효화 정책도 별도 회귀
테스트로 고정했고, 전체 offline suite는 25개 테스트를 통과했습니다.

| 동작 | working-tree 테스트 (`tests/test_cache_invalidation.py`) | 증거 상태 |
|---|---|---|
| UPDATE embedding 실패·재시도 | `test_embedding_failure_invalidates_related_cache_before_retry` | PASS, final receipt pending |
| UPDATE vector 실패·재시도 | `test_vector_failure_invalidates_related_cache_before_retry` | PASS, final receipt pending |
| CREATED embedding 실패·재시도·failure-window recache | `test_created_embedding_failure_retry_invalidates_failure_window_recache` | PASS, final receipt pending |
| CREATED vector 실패·재시도·failure-window recache | `test_created_vector_failure_retry_invalidates_failure_window_recache` | PASS, final receipt pending |
| 정상 same-hash corpus-wide 정책 | `test_normal_same_hash_reupload_invalidates_corpus_cache` | PASS, final receipt pending |
| 정상 UPDATE related/unrelated 정책 | `test_document_update_invalidates_related_cache_only` | PASS, final receipt pending |

이 결과는 아직 새 commit, 제출 저장소의 영구 verification receipt, Control
receipt 또는 live smoke로 승격되지 않았습니다. 아래 2026-07-28 문서는 그
당시 baseline의 역사적 영수증이며 이번 working-tree revision의 최종 증거로
해석하지 않습니다.

명령·환경·결과의 최소 영수증은
[`docs/verification/2026-07-28.md`](verification/2026-07-28.md)에 있습니다.
