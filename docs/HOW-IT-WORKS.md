# 동작 원리: 설계 이유와 내부 동작

이 서버는 사용자가 올린 문서(PDF·TXT·Markdown)를 근거로 질문에 답하는 API입니다. 이 글은 문서가 저장되고 질문에 답하기까지의 흐름을 따라가며, 그 과정에서 틀린 답이나 서로 어긋난 데이터가 생기지 않게 막는 장치 여섯 가지를 흐름 순서대로 설명합니다. 장치마다 「깨질 수 있던 것」, 「동작 방식」, 「고른 이유」를 같은 순서로 적었습니다.

> `파일:줄` 위치는 태그로 고정한 커밋 `15eb559` 기준이고, 경로는 저장소 루트 기준입니다.

## 과제가 요구한 것과 이 구현이 더한 것

| 구분 | 내용 |
|---|---|
| 과제가 요구한 것(`docs/PRD.md`) | 두 가지 이상 형식의 문서 업로드, 문서를 검색해 답하고 출처를 붙이기, 근거가 없는 질문에 답을 지어내지 않고 처리하기, 스트리밍 응답, 같거나 비슷한 질문의 답 재사용과 문서가 바뀌었을 때의 무효화, API 키 대신 구독형 CLI로 AI 부르기 |
| 이 구현이 더한 것 | 데이터 저장소를 PostgreSQL 하나로 옮긴 두 번째 구현(V2, `docs/PRD_PHASE2.md`), 같은 문서의 동시 업로드 처리, 설정 세 가지를 비교하는 답 재사용 조건, 답을 만드는 동안 바뀐 문서의 처리, 첫 글자 전에만 다시 시도하는 기준, 두 겹의 기권 |

## 처리 흐름

| 단계 | 하는 일 | 관련 장치 |
|---|---|---|
| ① 문서 저장 | 올린 문서를 작은 조각(chunk)으로 나누고, 조각마다 뜻을 숫자 목록으로 바꾼 값(embedding)을 계산해 PostgreSQL에 저장합니다 | 1, 2 |
| ② 저장된 답 확인 | 질문도 embedding으로 바꾸고, 같거나 비슷한 질문에 이미 만든 답(cache)이 있으면 AI를 부르지 않고 그 답을 돌려줍니다 | 3 |
| ③ 관련 조각 찾기 | 질문과 가장 비슷한 조각 5개를 찾고, 관련도 점수가 0.30보다 낮은 조각은 버립니다. 남은 조각이 없으면 답할 수 없다고 알립니다(기권) | 4 |
| ④ AI 답변 | 남은 조각과 질문을 prompt로 묶어 답을 만드는 AI(Codex App Server)에 보내고, 답이 만들어지는 대로 조금씩 사용자에게 보냅니다(SSE) | 5 |
| ⑤ 답 저장 | 완성된 답과 출처를 cache에 저장합니다. 서버가 기권한 답, 근거를 일부만 담아 만든 답, 답을 만드는 동안 문서 모음이 바뀐 답은 저장하지 않습니다 | 4, 6 |

## 용어

| 용어 | 이 글에서의 뜻 |
|---|---|
| chunk | 문서를 나눈 조각입니다. 기본 900자 이하이고, 검색과 출처 표시의 단위입니다 |
| embedding | 글의 뜻을 숫자 384개로 나타낸 값입니다. 두 값이 가까울수록 뜻이 비슷하다고 봅니다. 질문과 조각 사이의 가까움을 「관련도 점수」, 질문과 저장된 질문 사이의 가까움을 「유사도」라고 부릅니다. 둘 다 cosine similarity로 재며, 1이 가장 가깝습니다 |
| prompt | 서버가 질문과 찾은 조각을 묶어 AI에 보내는 지시문입니다. 사용자가 입력한 질문 자체가 아닙니다 |
| Codex App Server | 질문에 답하는 AI입니다. 과제가 API 키 대신 구독형 CLI를 쓰도록 정해서, 서버는 Codex CLI의 `app-server`를 하위 프로세스로 띄우고 stdio JSON-RPC로 주고받습니다. 코드를 작성할 때 쓴 코딩 도구 Codex와 같은 제품이지만, 여기서는 답을 만드는 데 씁니다 |
| delta | AI가 답을 만드는 동안 조금씩 보내오는 답의 일부분(몇 글자)입니다 |
| cache | 이미 만든 답과 출처를 저장해 두는 표(`answer_cache`)입니다 |
| 무효화 | 저장된 답을 지우지 않고 「더 이상 쓰지 않음」으로 표시하는 것입니다. 이유도 함께 남깁니다 |
| transaction | 여러 저장 작업을 하나로 묶어, 모두 성공하거나 모두 취소되게 하는 데이터베이스 기능입니다. 확정되기(commit) 전의 변경은 다른 요청에 보이지 않습니다 |
| advisory lock | 테이블 행 대신, 응용 프로그램이 정한 정수 key를 잠그는 PostgreSQL의 lock입니다. 같은 key를 잠그려는 작업은 앞 작업이 lock을 풀 때까지 기다립니다 |
| SSE | Server-Sent Events. 응답을 한 번에 보내지 않고 이어서 조금씩 보내는 HTTP 방식입니다 |

「version」과 「revision」은 아래 다섯 가지로 나뉩니다.

| 이름 | 무엇인가 | 정해지는 때 |
|---|---|---|
| 문서 version | 문서마다 1, 2, 3으로 올라가는 정수 | 같은 이름의 문서가 내용이나 처리 방식이 바뀐 채 다시 저장될 때 |
| corpus revision | 문서 모음 전체에 하나뿐인 정수(`corpus_state.revision`) | 문서가 새로 저장될 때마다 1씩 오릅니다. 「변경 없음」으로 끝난 업로드에서는 오르지 않습니다 |
| chunking version | 문서를 나누는 방식을 요약한 문자열 | 그 문서를 저장할 때의 설정으로 정해집니다. 설정만 바꾸면 이미 저장된 문서의 값은 그대로입니다 |
| indexing version | 나누는 방식과 embedding 모델을 함께 요약한 문자열 | chunking version과 같습니다 |
| cache의 세 version | 답을 만들 때의 prompt·embedding·검색 설정을 요약한 문자열 | 그 답을 저장할 때의 설정으로 정해집니다 |

## 1. 문서 저장: 한 transaction

문서 하나를 저장할 때 데이터베이스에 생기는 변경은 모두 한 transaction 안에서 함께 성공하거나 함께 취소됩니다.

**깨질 수 있던 것.** 첫 구현(V1)은 문서 정보와 cache를 SQLite에, 조각과 embedding을 Chroma에 따로 저장했습니다. 두 데이터 저장소에 쓰는 작업은 하나로 묶이지 않아서, 중간에 한쪽만 실패하면 문서 목록과 검색 결과가 서로 다른 문서 version을 가리킬 수 있었습니다. cache도 같은 문제를 안고 있었습니다. cache 무효화와 embedding 교체가 따로 실행되어, 그 사이에 들어온 질문이 옛 내용으로 cache를 다시 만들 수 있었습니다(`docs/adr/ADR-0002-storage-retrieval-and-cache.md` 「Trade-offs」).

태그로 고정한 버전은 이 문제 때문에 데이터 저장소를 PostgreSQL 하나로 옮긴 두 번째 구현(V2)입니다. 이 전환은 과제 요구가 아니라 이 구현의 설계 결정입니다(`docs/PRD_PHASE2.md`). V1 코드는 비교 기준으로 `src/officeagent/infrastructure/sqlite_store.py`·`chroma_store.py`에 남아 있고, V1 테스트는 V2 증거와 따로 셉니다(README 「테스트」).

**동작 방식.** V2는 PostgreSQL + pgvector 하나에 모든 것을 저장합니다. 문서를 조각으로 나누고 embedding을 계산하는 일은 transaction 밖에서 먼저 끝내고, 데이터베이스에 쓰는 일만 transaction 하나로 묶습니다. 옛 version은 지우지 않고 남겨 두므로, 문서마다 현재 version을 표시하는 칸 `ready`가 있습니다. 데이터베이스는 문서마다 `ready = true`인 행을 하나만 허용합니다. 저장은 다음 순서로 진행합니다.

1. 같은 이름의 현재 문서를 읽습니다(2절의 advisory lock을 얻은 뒤). 내용(hash)과 chunking·indexing version이 모두 같으면 아무것도 바꾸지 않고 「변경 없음(`UNCHANGED`)」으로 끝냅니다.
2. 새 문서 version을 아직 현재 version이 아니라는 표시(`ready = false`)와 함께 넣고, 조각과 embedding을 넣습니다.
3. 이 변경 때문에 틀린 답이 될 수 있는 저장된 답을 무효화합니다(3절).
4. 이전 version을 `ready = false`로 내린 뒤, 새 version을 `ready = true`로 올립니다. 「`ready`는 하나」 규칙 때문에 이 순서여야 합니다.
5. corpus revision을 1 올립니다(6절에서 씁니다).

중간에 오류가 나면 전부 취소되고, 이전 version과 저장된 답이 그대로 남습니다. commit 전의 변경은 다른 요청에 보이지 않으므로, 질문하는 쪽은 저장 도중의 문서를 볼 수 없습니다. 반쯤 저장된 문서를 숨기는 것은 transaction이고, `ready`는 남겨 둔 version 가운데 현재 것을 고르는 역할입니다. 검색은 `ready`인 version의 조각만 읽습니다.

**고른 이유.** 고른 방법은 PostgreSQL + pgvector 하나입니다. 비교한 방법 가운데 문서·조각·embedding·cache·revision을 transaction 하나로 함께 바꿀 수 있는 것은 이것뿐이었습니다(`docs/TRADEOFF_STORAGE_AND_DOCKER.md`). ADR-0004가 고르지 않은 방법과 그 이유는 다음과 같습니다.

| 방법 | 고르지 않은 이유 |
|---|---|
| SQLite + Chroma 유지 | 저장·백업·상태 확인의 경계가 둘로 남습니다. Chroma 공식 문서도 이 방식(PersistentClient)을 개발·테스트용으로 둡니다 |
| PostgreSQL + Chroma Server | Chroma의 운영 형태는 나아지지만, embedding과 문서 정보·cache를 한 번에 바꾸지 못하는 문제는 그대로입니다 |
| PostgreSQL + pgvector + Redis | cache가 병목이라는 측정 근거가 없는데, 무효화·만료·장애 지점이 하나 더 늘어납니다 |
| vector 전용 DB | 대규모 분산 검색이나 관리형 SLA가 필요할 때 의미가 있지만, 과제의 문서 규모에는 별도 서비스·자격 증명·이중 쓰기 절차가 지나칩니다 |

대가도 함께 적혀 있습니다. Docker와 PostgreSQL 준비, migration이 새로 필요합니다. 정확 검색(exact search)이라 문서가 아주 많아지면 속도를 보장하지 않습니다. 운영 구성은 단일 노드이고, standby 승격은 로컬 훈련 스크립트로만 해 보았습니다.

근거: `docs/adr/ADR-0004-postgresql-pgvector-storage.md`(「Alternatives considered」·「Costs and limits」)

**코드 위치**
- `src/officeagent/infrastructure/postgres_corpus_store.py:166-297`: `_commit_document`의 transaction
- 같은 파일 `:183-204`: 「변경 없음」 판정
- 같은 파일 `:335`: 검색의 `WHERE document.ready`
- `src/officeagent/migrations/0001_v2_schema.sql:13`, `:16-22`: 문서 primary key와 「`ready`는 하나」 규칙

**확인하는 테스트** (`tests/test_postgres_corpus_store.py`)
- `test_commit_is_atomic_and_pgvector_retrieval_tracks_ready_version`
- `test_chunk_failure_rolls_back_document_and_revision`
- `test_failure_after_cache_invalidation_rolls_back_every_write`

## 2. 같은 문서의 동시 업로드: advisory lock

같은 이름의 문서가 동시에 두 번 올라오면, advisory lock으로 차례를 정해 둘 다 정상으로 처리합니다.

**깨질 수 있던 것.** 두 요청이 동시에 「현재 version은 3」을 읽으면, 둘 다 version 4를 만들려 합니다. 데이터베이스는 같은 문서에 같은 version 번호를 두 번 허용하지 않으므로(primary key `(document_id, version)`), 늦은 요청은 오류로 끝납니다. 사용자는 정상 파일을 올렸는데도 실패를 받습니다. 처음 올라오는 문서도 같습니다. 문서 id를 파일 이름에서 만들기 때문에(uuid5), 두 요청이 같은 id로 version 1을 만들려다 한쪽이 실패합니다. 이 경우는 행 lock(`SELECT … FOR UPDATE`)으로도 막을 수 없습니다. 행 lock은 이미 있는 행만 잠그는데, 아직 잠글 행이 없기 때문입니다.

**동작 방식.**
- 파일 이름을 정규화하고(유니코드 정규화·대소문자 무시·공백 정리), 그 SHA-256 앞 8바이트를 정수로 바꿔 lock key로 씁니다.
- transaction을 시작하자마자 `pg_advisory_xact_lock(key)`를 겁니다. 같은 key를 원하는 요청은 여기서 기다립니다.
- lock을 얻은 다음에 현재 문서를 읽으므로, 늦은 요청은 앞 요청의 transaction이 끝난 뒤의 상태를 보고 판단합니다. 읽은 문서 행은 `FOR UPDATE`로도 잠급니다. 내용과 chunking·indexing version이 모두 같으면 「변경 없음」으로 끝나고, 하나라도 다르면 다음 version(5)으로 저장됩니다.
- transaction 단위 lock(`_xact_`)이라, commit하든 오류로 rollback하든 transaction이 끝나면 저절로 풀립니다.
- 다른 문서는 key가 달라서 이 lock에서는 기다리지 않습니다. 다만 모든 저장이 마지막에 corpus revision 한 행을 고치므로, 데이터베이스에 쓰는 마지막 구간은 결국 한 건씩 진행됩니다. 문서를 나누고 embedding을 계산하는 앞 구간은 병렬로 진행됩니다(`ARCHITECTURE.md:76-77`).

**고른 이유.** 처음 올라오는 문서는 잠글 행이 아직 없어서 행 lock으로는 차례를 정할 수 없습니다. advisory lock은 행이 없어도 파일 이름에서 만든 정수 key를 잠글 수 있습니다. 이 방식은 ADR-0004의 transaction 규칙 두 가지와도 맞습니다. 느린 작업(나누기·embedding)은 transaction 밖에서 끝내고 쓰기 직전에만 lock을 잡습니다. AI 호출과 SSE 동안에는 transaction이나 lock을 잡지 않습니다. 그래서 lock을 잡는 시간은 데이터베이스에 쓰는 짧은 구간뿐입니다. 데이터 저장소가 PostgreSQL 하나라서 이런 lock과 transaction을 한곳에서 함께 쓸 수 있습니다(`docs/TRADEOFF_STORAGE_AND_DOCKER.md`).

근거: `docs/adr/ADR-0004-postgresql-pgvector-storage.md`(「Transaction invariant」)

**코드 위치**
- `src/officeagent/infrastructure/postgres_corpus_store.py:168-181`: lock 획득과 현재 문서 조회
- 같은 파일 `:679-681`: `_advisory_lock_key`
- `src/officeagent/migrations/0001_v2_schema.sql:13`: primary key

**확인하는 테스트** (`tests/test_postgres_corpus_store.py`)
- `test_same_document_concurrency_serializes_to_one_revision`: 같은 내용을 동시에 두 번 올리면, 하나는 생성되고 하나는 「변경 없음」이며 revision은 1입니다
- `test_distinct_document_concurrency_preserves_both_revision_increments`: 다른 두 문서를 동시에 올리면 revision이 2까지 올라갑니다

## 3. 저장된 답의 재사용 조건: 세 가지 version

저장된 답은 그 답을 만들 때의 prompt·embedding·검색 설정을 요약한 세 version이 지금 값과 모두 같을 때만 재사용합니다. 문서가 바뀌면 영향을 받는 답을 무효화합니다.

**깨질 수 있던 것.** 문서가 그대로여도 설정이 바뀌면, 같은 질문에 이 서버가 만들 답이 달라집니다. prompt의 규칙이 바뀌면 답의 기준이 달라지고, embedding 모델이 바뀌면 「비슷한 질문」과 「관련 조각」의 판단이 달라지며, 검색 설정이 바뀌면 근거가 되는 조각이 달라집니다. 예전 설정으로 만든 답을 내보내면, 지금 설정이라면 나오지 않았을 답이 나갑니다. 문서가 바뀌는 경우도 마찬가지입니다. 다만 이 장치는 저장된 답만 다룹니다. 이미 저장된 조각의 embedding은 그 문서를 다시 올려야 새 설정으로 바뀝니다(알려진 한계).

**동작 방식.**
- 저장된 답마다 세 version을 함께 저장합니다.
  - `prompt_version`: prompt template, token 계산 방식, 크기 상한
  - `embedding_version`: embedding 모델
  - `retrieval_version`: 가져올 조각 수(5), 최소 점수(0.30), 비슷한 질문 기준(0.95), chunking 방식
- 찾는 순서: 질문을 정규화해(유니코드 정규화·대소문자 무시·공백 정리) 똑같은 질문(exact)을 먼저 찾고, 없으면 유사도가 0.95 이상인 질문(similar)을 찾습니다. 두 경우 모두 세 version이 지금 값과 같아야 합니다. 비슷한 질문의 답을 재사용하는 것은 과제 요구사항입니다(「동일하거나 유사한 질문에 대해 LLM을 재호출하지 않도록」, `docs/PRD.md`).
- 찾기 전에 version이 다른 답을 무효화하고 이유(`version_mismatch`)를 남깁니다. 정확성은 앞에서 말한 version 조건이 지키고, 이 단계는 낡은 답을 기록으로 남기는 역할입니다.
- 문서가 저장될 때는 아래 표에서 위에서부터 처음 맞는 줄 하나를 적용합니다. 「바뀐 것」은 저장된 문서의 값과 지금 설정·내용을 비교한 결과입니다.

| 바뀐 것 | 무효화하는 답 | 남기는 이유 |
|---|---|---|
| chunking 방식 | 전체 | `chunking_configuration_changed` |
| indexing 방식(chunking 방식 + embedding 모델) | 전체 | `indexing_configuration_changed` |
| 새 문서 추가 | 전체 | `corpus_expanded` |
| 저장된 답 가운데 지금 설정과 version이 다른 답이 하나라도 있음 | 전체 | `configuration_mismatch` |
| 기존 문서의 내용 | 그 문서를 출처로 쓴 답, 그리고 새 조각과의 관련도 점수가 검색 최소 점수(0.30) 이상인 질문의 답 | `document_updated`, `new_evidence_relevant` |

**고른 이유.**
- 세 version을 비교하는 이유는 설정을 바꾼 뒤 오래된 답을 재사용하지 않기 위해서입니다(DEC-011, `docs/DECISION_LOG.md`). 대가로, 설정 조합마다 cache 재사용이 줄고 구현·테스트 범위가 늘어납니다(ADR-0004 「P0 pin-point」).
- 0.95와 0.30은 둘 다 초기값이며, 최적값이라고 주장하지 않습니다(ADR-0004 「Retrieval and cache decision」). 0.95는 다른 질문의 답을 잘못 재사용하는 일을 줄이려고 보수적으로 잡은 값입니다(ADR-0002 「Trade-offs」).
- cache를 Redis가 아니라 PostgreSQL 표에 두는 이유는 1절 표의 세 번째 줄과 같습니다.

**코드 위치**
- `src/officeagent/application/question_answering.py:54-69`: `build_retrieval_version`
- `src/officeagent/infrastructure/postgres_corpus_store.py:359-425`: `_find_cached_answer`
- 같은 파일 `:516-633`: `_invalidate_for_change`
- `src/officeagent/bootstrap.py:78`: 갱신 때의 관련도 기준을 검색 최소 점수와 같게 연결

**확인하는 테스트**
- `test_cache_requires_all_versions_and_revision_guard_skips_stale_write`(`tests/test_postgres_corpus_store.py`)
- `test_update_invalidates_newly_relevant_non_dependency_cache`(같은 파일)
- `test_prompt_version_change_uses_full_invalidation_fallback`(같은 파일)
- `test_http_rag_exact_similar_cache_and_document_invalidation`(`tests/test_v2_offline_paths.py`): 실제 PostgreSQL에 HTTP로 요청을 보내, exact·similar 재사용과 문서 갱신 뒤 무효화를 확인합니다

## 4. 기권: 답할 근거가 부족할 때

답할 근거가 부족하다고 판단되면 답할 수 없다고 알립니다. 서버가 AI를 부르기 전에 멈추는 경우와, prompt 규칙으로 AI가 스스로 밝히게 하는 경우가 있습니다.

**깨질 수 있던 것.** 관련 문서가 없는데 AI를 부르면, 그럴듯하지만 근거 없는 답이 나옵니다.

**동작 방식.**

1. **서버가 하는 기권**
   - 관련도 점수가 0.30 이상인 조각이 하나도 없으면, AI를 부르지 않고 이렇게 답합니다: 「업로드된 문서에서 이 질문에 답할 충분한 근거를 찾지 못했습니다.」 이 답은 cache에 저장하지 않습니다.
   - 서버는 prompt를 token 수로 세어 상한(4096 token)을 넘지 않게, 관련도 순서대로 통째로 들어가는 조각만 담습니다. 조각이 있어도 하나도 담을 수 없으면 다른 정해진 문장으로 답합니다: 「검색된 근거를 안전한 프롬프트 크기 안에 담을 수 없어 답변을 생성하지 않았습니다.」 이 답도 저장하지 않습니다.
   - 일부만 담아 답했다면 답은 보내되 cache에는 저장하지 않습니다(`prompt_evidence_reduced`). 근거 일부가 빠진 채 만든 답이 이후 같거나 비슷한 질문에 재사용되지 않게 하기 위해서입니다.
2. **AI가 하는 기권:** 점수는 넘었지만 조각에 답이 없으면, prompt 규칙 3(`If the evidence is insufficient, say that the uploaded documents do not establish the answer.`)으로 AI에게 문서로 확인되지 않는다고 답하도록 지시합니다. AI가 이 지시를 따랐는지는 서버가 검사하지 않습니다. 이 답은 정상 답과 같은 경로를 거치므로 출처와 함께 cache에 저장됩니다.

출처 표시는 AI가 만들지 않습니다. 서버가 AI에 보낸 조각의 파일 이름·문서 version·페이지와 글자 위치로 만듭니다.

**고른 이유.** 문서 밖의 답과 AI가 지어낸 출처를 막기 위해서입니다(DEC-008, `docs/DECISION_LOG.md`). 다만 0.30은 초기값이고 평가로 정한 최적값이 아닙니다. 그래서 문서에 답이 있어도 점수가 0.30보다 낮으면 기권할 수 있고, 반대로 관련 없는 조각이 0.30을 넘을 수도 있습니다(ADR-0004 「Retrieval and cache decision」).

**코드 위치**
- `src/officeagent/application/question_answering.py:44-49`: 기권 문장 두 개
- 같은 파일 `:207-229`: 근거 없음
- 같은 파일 `:233-262`: prompt 크기 초과
- 같은 파일 `:294-296`: 일부만 담은 답의 저장 생략
- `src/officeagent/application/prompts.py:130`: prompt 규칙 3

**확인하는 테스트**
- `test_http_rag_exact_similar_cache_and_document_invalidation`(`tests/test_v2_offline_paths.py`): 근거 없음 답과 `cache_skip_reason = "no_evidence"`
- `test_question_path_abstains_without_llm_or_cache_when_prompt_budget_exhausts`(`tests/test_prompt_packing.py`)

## 5. 스트리밍 중 오류: 첫 글자 전에만 재시도

AI와의 연결이 끊기거나 응답이 늦어지면, 답이 사용자에게 한 글자도 나가지 않았을 때만 한 번 더 시도합니다.

**깨질 수 있던 것.** 답의 일부가 이미 나간 뒤에 다시 시도하면, 사용자 화면에 앞 시도의 일부와 새 시도의 전체가 이어 붙어 보입니다. 이미 보낸 부분을 다시 만드는 비용도 듭니다.

**동작 방식.**
- 시도할 때마다 Codex App Server 프로세스를 새로 띄워 연결(session)을 엽니다. 최대 두 번 시도합니다.
- 다시 시도하는 경우: 호출 한 번에서 첫 글자를 45초 안에 받지 못했거나, 프로세스와 주고받는 통로에 오류가 났을 때입니다. 단, 화면에 보일 글자가 아직 하나도 나가지 않았을 때만입니다. 이 재시도도 호출을 한 번 더 쓰지만, 사용자에게 아직 아무것도 나가지 않았으므로 답이 섞이지 않습니다.
- 「화면에 보일 첫 글자」의 기준: 서버는 AI에게 답을 `{"answer": "…"}` 형태의 JSON으로만 쓰도록 요구하고(`outputSchema`), 이 JSON을 풀어서 답 글자만 사용자에게 보냅니다. 그래서 `{"answer":"`까지만 받고 끊긴 경우는 아직 아무것도 보이지 않았으므로 다시 시도합니다.
- 다시 시도하지 않는 경우: 한 글자라도 나간 뒤의 오류, 그리고 AI가 답하기를 실패로 끝낸 경우(failed turn)입니다.
- HTTP 200과 응답 header는 처음에 이미 나갔으므로, 그 뒤의 오류는 status code로 알릴 수 없습니다. 그래서 서버는 오류를 SSE `error` 이벤트로 보냅니다. 스트리밍을 쓰지 않는 JSON 응답 경로(`/v1/questions`)에서는 같은 오류가 502로 나갑니다.

**고른 이유.** 이미 보낸 출력을 다시 만들지 않으면 중복 답변과 이중 비용을 막을 수 있습니다. 대가로, 답이 중간에 끊기면 그 답은 복구하지 못합니다(`docs/adr/ADR-0003-codex-app-server-streaming.md` 「How」·「Trade-offs」). 이 기준은 「서버가 첫 글자를 내보냈는가」입니다. 사용자가 실제로 받았는지, 정확히 한 번 전달되었는지는 보장하지 않습니다(`docs/adr/ADR-0005-codex-structured-output-streaming.md`).

**코드 위치**
- `src/officeagent/infrastructure/codex_app_server.py:302-337`: `stream_answer`
- `src/officeagent/infrastructure/structured_answer.py:31-39`: `{"answer": string}` 형식
- `src/officeagent/api/app.py:194-213`: SSE `error` 이벤트
- 같은 파일 `:169-174`: JSON 응답 경로의 502

**확인하는 테스트** (`tests/test_codex_protocol.py`)
- `test_timeout_before_first_delta_retries_once_with_new_session`
- `test_structured_prefix_does_not_block_pre_visible_delta_retry`
- `test_timeout_after_delta_does_not_retry`
- `test_os_error_after_visible_delta_does_not_retry`
- `test_failed_turn_is_rejected_without_retry`

## 6. 답을 만드는 동안 바뀐 문서: revision 비교

AI가 답을 만드는 동안 문서가 바뀌면, 그 답은 사용자에게 보내되 cache에는 저장하지 않습니다.

**깨질 수 있던 것.** 답 하나를 만드는 데는 시간이 걸립니다. 서버는 AI 호출 한 번마다 첫 글자까지 최대 45초, 답이 끝날 때까지 최대 120초를 기다립니다. 그동안 다음 순서가 일어날 수 있습니다.

1. 질문 A가 문서 version 3의 조각을 찾습니다.
2. AI가 답을 쓰는 동안 문서가 version 4로 저장되고, 관련된 답의 무효화가 끝납니다.
3. A의 답(version 3을 근거로 만든 답)이 그 뒤에 cache에 저장됩니다.

무효화는 이미 끝났으므로, 이 답은 무효화 표시 없이 쓸 수 있는 상태로 남습니다. 그러면 다음에 같은 질문을 한 사람은 옛 문서로 만든 답을 받게 됩니다.

**동작 방식.**
1. 조각을 찾을 때 corpus revision을 함께 읽습니다. `FOR SHARE`로 읽으므로 검색하는 동안에는 다른 저장이 revision을 바꾸지 못하고, 검색 결과와 revision이 같은 시점을 가리킵니다.
2. AI 호출과 SSE 동안에는 transaction도 lock도 잡지 않습니다.
3. 답을 저장할 때는 질문 key로 advisory lock을 걸고, revision을 `FOR UPDATE`로 다시 읽습니다. 검색 때와 다르면 저장하지 않고, 응답의 `cache_skip_reason`에 `corpus_changed`를 남깁니다.
4. 사용자는 검색 시점의 문서를 근거로 만든 답을 그대로 받습니다. 이 답은 cache에 남지 않으므로, 다음에 같은 질문이 오면 새 문서로 답을 다시 만듭니다.

**고른 이유.** 답을 만드는 동안 lock을 잡고 있으면, 그 시간(호출 한 번에 최대 120초) 동안 다른 문서 저장이 모두 멈춥니다. 그래서 답을 만드는 동안에는 lock을 잡지 않고, 저장 직전에 revision 번호를 비교합니다(`docs/PRD_PHASE2.md` 「2.3 안전한 cache 저장」, `ARCHITECTURE.md` 「3. 검색·생성·cache race」). 대가로, revision이 문서 모음 전체에 하나뿐이라 질문과 관계없는 문서가 저장되어도 그동안 만든 답은 저장되지 않습니다.

**코드 위치**
- `src/officeagent/infrastructure/postgres_corpus_store.py:316`: 검색할 때 revision 읽기
- 같은 파일 `:440-452`: 저장 직전 lock과 revision 비교
- `src/officeagent/application/question_answering.py:294-323`
- `src/officeagent/infrastructure/settings.py:62-63`: 45초·120초(`src/officeagent/infrastructure/codex_app_server.py:372`에서 호출마다 시작 시각을 잽니다)

**확인하는 테스트**
- `test_cache_requires_all_versions_and_revision_guard_skips_stale_write`(`tests/test_postgres_corpus_store.py`)

## 알려진 한계

아래는 2026.10 문서 보강 때 코드를 다시 읽으며 찾은 것입니다. 재현 테스트는 없습니다.

- **lock 순서:** 업로드는 문서 행을 `FOR UPDATE`로 잠그고(2절), 마지막에 revision 행을 고칩니다. cache 저장은 반대로 revision 행을 먼저 잠근 뒤(6절), 출처 기록 표(`cache_dependencies`)에 행을 넣습니다. 이 표에는 문서 행을 가리키는 외래 키(FK)가 있어서, 행을 넣을 때 그 문서 행에 lock을 요청합니다. 그래서 문서 D를 갱신하는 업로드와 D를 출처로 쓴 답의 저장이 겹치면, 두 작업이 서로 상대가 잡은 lock을 기다리는 교착(deadlock)이 생길 수 있습니다. PostgreSQL은 이를 찾아 둘 중 하나를 취소합니다. 업로드가 취소되면 업로드 요청이 500 오류로 끝나고, 저장이 취소되면 답과 출처를 다 보낸 뒤 SSE `error`가 나갑니다(JSON 응답 경로에서는 502).
- **revision이 하나뿐:** 질문과 관계없는 문서가 저장되어도 그동안 만든 답은 cache에 저장되지 않습니다(6절).
- **겹치는 lock:** cache 저장은 revision 행 lock으로 이미 한 건씩 진행되므로, 함께 거는 질문 key advisory lock(`postgres_corpus_store.py:440-443`)이 따로 막는 경우는 없습니다.
- **새 문서 추가 때 전체 무효화:** 문서 갱신 때처럼 관련도로 범위를 좁히지 않아, 관계없는 답까지 무효화됩니다(3절 표).
- **설정 변경 뒤 재색인 없음:** embedding 모델을 바꿔도 이미 저장된 조각의 embedding은 다시 계산하지 않고, 검색할 때도 indexing version으로 조각을 거르지 않습니다. 그래서 다시 올리지 않은 문서는 옛 모델의 embedding으로 새 모델의 질문과 비교됩니다. 설정을 바꾼 뒤에는 각 파일을 같은 이름으로 다시 올려야 합니다(README 「설정」).
- **비슷한 질문의 답 재사용:** similar로 맞으면 다른 질문의 답과 출처를 그대로 돌려줍니다. 뜻이 다른 질문이 0.95를 넘는 경우가 있는지는 평가로 확인하지 않았습니다.
- **조회할 때의 무효화:** 설정이 다른 서버 프로세스 두 개가 같은 데이터베이스를 쓰면, 조회할 때마다 서로의 답을 무효화합니다(3절).
- **하지 않은 것:** 성능 측정(benchmark), 자동 장애 전환(automatic failover)과 운영 환경의 고가용성(HA), 이미지 PDF의 OCR, 답의 문장마다 출처가 뒷받침하는지 확인하는 검증은 하지 않았습니다. 로컬에서 standby를 수동으로 승격해 보는 훈련(`src/officeagent/infrastructure/postgres_ha_drill.py`)은 있습니다. 범위는 README 「최신 상태와 비목표」와 같습니다.
