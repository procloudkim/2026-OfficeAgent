# ADR-0002: 저장소, 검색, 캐시

> V1 SQLite/Chroma rollback 기준선입니다. 검증된 V2 PostgreSQL/pgvector overlay와
> P0-08 decision lifecycle은 [ADR-0004](./ADR-0004-postgresql-pgvector-storage.md)가
> 소유합니다.

| 항목 | 내용 |
|---|---|
| Status | Accepted — source baseline `0565fa5` offline verified; live smoke historic at `e838cc7` |
| Date | 2026-07-29 |

## Why

문서 기반 답변은 벡터 검색을 실제로 수행해야 하며, 동일·유사 질문의 LLM 호출을
줄이고 문서 변경 시 관련 캐시를 무효화해야 한다.

## What

- SQLite(`aiosqlite`)는 문서 메타데이터와 `answer_cache`를 보관한다.
- Chroma `PersistentClient`는 청크와 임베딩을 보관한다.
- 운영 임베딩은 FastEmbed의 다국어 MiniLM 384차원 모델을 사용한다.
  테스트에서는 결정론적 fake 임베딩을 사용한다.
- 캐시는 정규화 질문의 active exact match를 먼저 찾고, 없으면 active 질문
  embedding 중 cosine similarity가 `0.95` 이상인 최상위 항목만 재사용한다.
- metadata upsert 직후 `CREATED`와 `UNCHANGED`는 모든 캐시를 무효화한다.
  `UPDATED`는 해당 문서에 의존하는 캐시만 무효화한다. 세 상태 모두 이후
  embedding과 vector re-index를 수행한다.
- `UNCHANGED`의 corpus-wide 정책은 정상 same-hash 재업로드뿐 아니라 metadata
  commit 뒤 embedding/vector가 실패한 재시도도 포함한다. 현재 schema에는 두
  경우를 구분하는 vector-readiness 상태가 없다.

## How

- 관계형 테이블은 `documents`, `answer_cache`, `cache_dependencies`로 한다.
  문서 청크는 중복 저장하지 않고 Chroma만 소유한다.
- 문서 ID는 파일명 기반 UUID5로 안정적으로 유지한다. 원본 byte SHA-256이
  바뀔 때만 version을 증가시킨다.
- Chroma collection은 스키마 변경을 드러내는 `document_chunks_v1`로 하고,
  cosine 공간을 명시한다.
- answer cache는 citation이 가리키는 document ID를 dependency table에
  기록한다. `UPDATED`이면 그 dependency의 active row만 `valid=0`으로 바꾸고,
  `CREATED`/`UNCHANGED`이면 모든 active row를 무효화한다.
- SQLite document upsert와 cache 무효화는 failure-prone embedding/vector
  작업보다 먼저 실행한다. 첫 `CREATED`가 실패한 뒤 old corpus로 cache가 다시
  만들어져도, 같은 byte retry는 `UNCHANGED` 전체 무효화 후 re-index하여 그
  순차 failure-window cache를 제거한다.
- cache 응답은 `cache_hit`와 `cache_match=exact|similar`를 포함한다.

## Trade-offs

SQLite와 Chroma 두 저장소를 운영해야 하지만, 메타데이터/캐시와 벡터 검색을
각각 단순하게 검증할 수 있다. 두 저장소 사이의 분산 트랜잭션은 없고 Chroma의
문서 교체도 delete와 upsert가 원자적이지 않다. 실패 창에는 이전, 부분 또는 빈
vector 상태가 노출될 수 있으며, 무효화와 vector 교체 사이의 동시 질의가 cache를
다시 만드는 race도 남는다. 동일 질문의 동시 cache miss를 합치는 single-flight도
없어 Codex 중복 호출과 경쟁 cache write가 가능하다.

모든 `UNCHANGED`에서 corpus 전체를 무효화하므로 정상 same-hash 재업로드에서도
관련 없는 cache까지 비운다. 이는 readiness/보상 API를 새로 도입하지 않고
실패·재시도 정확성을 우선한 보수적 비용이다. `0.95`는 오답 재사용을 보수적으로
줄이는 초기값이며, fixture 기반 평가로 변경할 수 있다.

별도 pending/ready schema, outbox 또는 saga는 이번 범위에서 도입하지 않았다.
schema migration과 새로운 복구 프로토콜이 필요하고, Chroma에 현재
document/version vector가 일부 존재한다는 사실만으로 delete→upsert 완료를
판정할 수 없기 때문이다.

## Evidence

- 규칙: `REQ-04`, `REQ-10`, `REQ-11`, `REQ-12`, `REQ-13`, `TEST-02`, `TEST-03`.
- 의존성: `pyproject.toml`에 `aiosqlite`, `chromadb`, `fastembed`가 선언돼 있다.
- 구현: `infrastructure/sqlite_store.py`, `chroma_store.py`,
  `embeddings.py`.
- 자동화: 실제 SQLite/Chroma를 쓰는 `test_sqlite_store.py`,
  `test_chroma_store.py`, `test_cache_invalidation.py`. Cache remediation source
  baseline `0565fa5`와 같은 source tree에서 embedding failure와 vector-store
  경계 failure를 주입한 `CREATED` failure-window retry, 정상 same-hash 정책을
  포함한 offline suite 25개가 통과했다. Chroma 내부 delete→upsert 중간 실패를
  직접 주입한 증거는 아니다.
- 증거 경계: 이 offline suite는 구현과 순차 failure/retry 경로를 검증한다.
  SQLite/Chroma cross-store 원자성, concurrent recache와 Chroma 내부
  partial-upsert는 검증하지 않는다. 2026-07-28 `e838cc7` live smoke는 Uvicorn
  upload → FastEmbed → Chroma → Codex App Server → SSE 경로를 확인한 역사적
  증거이며 이후 source에서 재실행하지 않았다.
