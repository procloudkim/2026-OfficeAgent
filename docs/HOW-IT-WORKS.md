# 동작 원리: 왜 이렇게 만들었고, 안에서 어떻게 움직이는가

> **초안 상태:** 이 문서는 Claude Code가 코드를 읽고 쓴 초안입니다. 지원자가 자기 말로 고쳐 쓴 뒤 이 인용 블록을 지웁니다.
> 줄 번호는 제출본 `15eb559` 기준입니다.

이 문서는 핵심 장치 여섯 가지를 같은 순서로 설명합니다: **무엇이 깨질 수 있었나 → 코드가 하는 일 → 코드 위치 → 확인하는 테스트 → 면접에서 나올 질문.**

## 1. 문서·벡터·cache를 한 transaction으로 저장한다

**무엇이 깨질 수 있었나.** 초기 기준선은 metadata를 SQLite에, 벡터를 Chroma에 따로 썼습니다. 두 저장소는 하나의 transaction으로 묶이지 않아서, 한쪽만 성공하면 문서 목록과 검색 결과가 서로 다른 버전을 가리킬 수 있었습니다.

**코드가 하는 일.** PostgreSQL + pgvector 하나로 옮기고, 업로드 한 번을 transaction 하나로 처리합니다.
1. 새 버전 행을 `ready = false`로 넣습니다.
2. chunk와 embedding을 같은 transaction 안에서 넣습니다.
3. 이 변경 때문에 낡게 된 cache를 무효화합니다(3절).
4. 이전 버전을 `ready = false`로, 새 버전을 `ready = true`로 바꿉니다.
5. `corpus_state.revision`을 1 올립니다.

중간에 예외가 나면 다섯 단계가 모두 rollback됩니다. 검색은 `ready`인 버전만 읽기 때문에, 반쯤 저장된 문서는 검색에 나오지 않습니다.

**코드 위치**
- `src/officeagent/infrastructure/postgres_corpus_store.py:166-297`: `_commit_document`의 transaction
- 같은 파일 `:335`: 검색의 `WHERE document.ready`

**확인하는 테스트** (`tests/test_postgres_corpus_store.py`)
- `test_commit_is_atomic_and_pgvector_retrieval_tracks_ready_version`
- `test_chunk_failure_rolls_back_document_and_revision`
- `test_failure_after_cache_invalidation_rolls_back_every_write`

**면접 질문**
- 두 저장소를 쓰면서 정합성을 지키려면 어떤 방법이 있었나요? 왜 저장소를 하나로 줄이는 쪽을 골랐나요?
- 새 버전을 먼저 `ready = false`로 넣는 이유는 무엇인가요?

## 2. 같은 문서의 동시 업로드는 advisory lock으로 한 줄로 세운다

**무엇이 깨질 수 있었나.** 같은 파일이 동시에 두 번 올라오면, 두 요청이 모두 "현재 버전 = N"을 읽고 둘 다 N+1을 만들려 할 수 있습니다. 처음 올라오는 문서는 더 위험합니다. 잠글 행이 아직 없어서 `SELECT … FOR UPDATE`가 아무것도 잠그지 못하기 때문입니다.

**코드가 하는 일.** 정규화한 파일 이름의 SHA-256 앞 8바이트를 정수 key로 만들고, transaction 시작 직후 `pg_advisory_xact_lock(key)`를 겁니다. 같은 문서의 요청은 이 key에서 기다리고, 앞 요청이 commit이나 rollback으로 끝나면 lock이 자동으로 풀립니다. lock을 얻은 뒤 현재 버전을 다시 읽기 때문에, 뒤 요청은 앞 요청의 결과를 보고 판단합니다. 다른 문서는 key가 달라서 서로 기다리지 않습니다.

**코드 위치**
- `postgres_corpus_store.py:168-181`: lock 획득과 현재 버전 재조회
- 같은 파일 `:679-681`: `_advisory_lock_key`
- `ARCHITECTURE.md:76`

**확인하는 테스트**
- `test_same_document_concurrency_serializes_to_one_revision`
- `test_distinct_document_concurrency_preserves_both_revision_increments`

**면접 질문**
- 행 lock(`FOR UPDATE`)만으로는 왜 부족했나요?
- session lock이 아니라 transaction lock(`_xact_`)을 쓴 이유는 무엇인가요? 예외가 나면 lock은 어떻게 되나요?

## 3. cache는 세 가지 version이 모두 맞을 때만 쓴다

**무엇이 깨질 수 있었나.** prompt, embedding 모델, 검색 설정 중 하나만 바뀌어도, 예전 설정으로 만든 답이 cache에서 그대로 나갈 수 있습니다.

**코드가 하는 일**
- cache 행마다 `prompt_version`·`embedding_version`·`retrieval_version`을 저장합니다.
- `retrieval_version`에는 검색 개수, 최소 점수, cache 유사도 기준, chunking 정책의 digest가 모두 들어갑니다.
- 조회할 때는 먼저 version이 다른 행을 비활성화합니다. 그다음 같은 질문(exact)을 찾고, 없으면 cosine 유사도 0.95 이상인 질문(similar)을 찾습니다. 두 경우 모두 세 version이 현재 값과 같아야 합니다.
- 문서가 바뀌면 바뀐 범위에 맞춰 무효화합니다.
  - chunking 정책이 바뀌었거나, indexing 정책이 바뀌었거나, 새 문서가 추가되었거나, 설정이 맞지 않으면 → cache 전체
  - 기존 문서만 갱신되었으면 → 그 문서를 출처로 쓴 cache, 그리고 새 chunk와 질문이 기준 이상으로 비슷한 cache

**코드 위치**
- `src/officeagent/application/question_answering.py:54-69`: `build_retrieval_version`
- `postgres_corpus_store.py:359-425`: `_find_cached_answer`
- 같은 파일 `:516-633`: `_invalidate_for_change`

**확인하는 테스트**
- `test_cache_requires_all_versions_and_revision_guard_skips_stale_write`
- `test_update_invalidates_newly_relevant_non_dependency_cache`
- `test_prompt_version_change_uses_full_invalidation_fallback`
- `test_document_update_invalidates_related_cache_only`(`tests/test_cache_invalidation.py`)

**면접 질문**
- 새 문서가 추가되면 왜 cache 전체를 지우나요? 범위를 좁힐 수는 없었나요?
- 출처로 쓰지 않은 cache까지 무효화하는(`new_evidence_relevant`) 이유는 무엇인가요?

## 4. 늦게 도착한 cache write를 막는다

**무엇이 깨질 수 있었나.** 질문 A가 검색을 마치고 LLM 답을 기다리는 사이에 문서가 갱신될 수 있습니다. 그러면 무효화는 이미 끝났는데, A의 답(옛 근거로 만든 답)이 그 뒤에 cache에 들어갑니다. 무효화를 거치지 않은 낡은 cache가 생기는 것입니다.

**코드가 하는 일**
1. 검색할 때 `corpus_state.revision`을 `FOR SHARE`로 읽어, 검색 결과와 revision이 같은 시점을 가리키게 합니다.
2. 답을 cache에 쓸 때는 질문 key로 advisory lock을 걸고, revision을 `FOR UPDATE`로 다시 읽습니다.
3. revision이 검색 때와 다르면 쓰지 않고 `cache_skip_reason = "corpus_changed"`를 남깁니다. 사용자는 답을 그대로 받고, cache에만 남지 않습니다.
4. prompt 크기 때문에 근거 일부만 썼다면(`prompt_evidence_reduced`), 빠진 출처를 숨긴 cache가 생기지 않도록 역시 쓰지 않습니다.

**코드 위치**
- `postgres_corpus_store.py:316`: 검색의 revision 읽기
- 같은 파일 `:440-452`: cache write 직전 revision 확인
- `question_answering.py:294-323`

**확인하는 테스트**
- `test_cache_requires_all_versions_and_revision_guard_skips_stale_write`

**면접 질문**
- 이 경쟁 상태를 문서 업로드 쪽 lock만으로 막을 수 없는 이유는 무엇인가요?
- cache에 쓰지 않은 답을 사용자에게 돌려주는 것은 괜찮은가요?

## 5. SSE는 첫 글자가 나가기 전에만 다시 시도한다

**무엇이 깨질 수 있었나.** 응답이 일부 나간 뒤에 다시 시도하면, 사용자 화면에 앞 시도의 일부와 새 시도의 전체가 섞여 보입니다.

**코드가 하는 일**
- 최대 두 번 시도하고, 시도할 때마다 새 Codex App Server session을 엽니다.
- 첫 delta가 나가기 전에 timeout이나 전송 오류가 나면 한 번만 다시 시도합니다.
- 한 글자라도 나간 뒤의 오류는 다시 시도하지 않고 그대로 실패로 올립니다.
- 모델이 turn 실패를 돌려주면 다시 시도하지 않습니다.
- HTTP 200과 헤더는 이미 나갔기 때문에, API는 오류를 SSE `error` 이벤트로 보냅니다.

**코드 위치**
- `src/officeagent/infrastructure/codex_app_server.py:302-334`: `stream_answer`
- `src/officeagent/api/app.py:194-213`: SSE 오류 이벤트

**확인하는 테스트** (`tests/test_codex_protocol.py`)
- `test_timeout_before_first_delta_retries_once_with_new_session`
- `test_timeout_after_delta_does_not_retry`
- `test_os_error_after_visible_delta_does_not_retry`
- `test_failed_turn_is_rejected_without_retry`

**면접 질문**
- 재시도를 한 번으로 제한한 이유는 무엇인가요?
- 스트리밍 중에 오류가 나면 HTTP status code로 알릴 수 없는 이유는 무엇인가요?

## 6. 근거가 없으면 답하지 않는다(기권)

**무엇이 깨질 수 있었나.** 관련 문서가 없는데 LLM을 부르면, 그럴듯하지만 근거 없는 답이 나옵니다.

**코드가 하는 일**
- 최소 관련도 점수를 넘는 chunk가 없으면, LLM을 부르지 않고 정해진 문장을 돌려줍니다: 「업로드된 문서에서 이 질문에 답할 충분한 근거를 찾지 못했습니다.」 이 답은 cache에도 넣지 않습니다.
- 근거를 prompt 크기 안에 하나도 담을 수 없을 때도 같은 방식으로 기권합니다.

**코드 위치**
- `question_answering.py:44-49`: 기권 문장
- 같은 파일 `:207-229`: 근거 없음
- 같은 파일 `:233-262`: prompt 크기 초과

**면접 질문**
- 최소 관련도 점수는 어떻게 정했나요? 너무 높거나 낮으면 무엇이 달라지나요?
- 기권한 답을 cache에 넣지 않는 이유는 무엇인가요?

## 이 문서가 다루지 않는 것

성능 benchmark, 운영 환경의 HA, OCR, 의미 수준의 답변 검증은 하지 않았습니다. 범위는 README의 「최신 상태와 비목표」 절과 같습니다.
