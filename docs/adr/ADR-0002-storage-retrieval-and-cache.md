# ADR-0002: 저장소, 검색, 캐시

| 항목 | 내용 |
|---|---|
| Status | Accepted — implemented and automated-tested |
| Date | 2026-07-28 |

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
- 새 corpus 생성은 모든 캐시를 무효화한다. 같은 파일명으로 문서를 갱신하면 해당
  문서에 의존하는 캐시만 무효화한다.

## How

- 관계형 테이블은 `documents`, `answer_cache`, `cache_dependencies`로 한다.
  문서 청크는 중복 저장하지 않고 Chroma만 소유한다.
- 문서 ID는 파일명 기반 UUID5로 안정적으로 유지한다. 원본 byte SHA-256이
  바뀔 때만 version을 증가시킨다.
- Chroma collection은 스키마 변경을 드러내는 `document_chunks_v1`로 하고,
  cosine 공간을 명시한다.
- answer cache는 citation이 가리키는 document ID를 dependency table에
  기록한다. 갱신 시 그 dependency의 active row만 `valid=0`으로 바꾼다.
- cache 응답은 `cache_hit`와 `cache_match=exact|similar`를 포함한다.

## Trade-offs

SQLite와 Chroma 두 저장소를 운영해야 하지만, 트랜잭션성 메타데이터/캐시와 벡터
검색을 각각 단순하게 검증할 수 있다. `0.95`는 오답 재사용을 보수적으로 줄이는
초기값이며, fixture 기반 평가로 변경할 수 있다.

## Evidence

- 규칙: `REQ-04`, `REQ-10`, `REQ-11`, `REQ-12`, `REQ-13`, `TEST-02`, `TEST-03`.
- 의존성: `pyproject.toml`에 `aiosqlite`, `chromadb`, `fastembed`가 선언돼 있다.
- 구현: `infrastructure/sqlite_store.py`, `chroma_store.py`,
  `embeddings.py`.
- 자동화: 실제 SQLite/Chroma를 쓰는 `test_sqlite_store.py`,
  `test_chroma_store.py`, `test_cache_invalidation.py`.
- 실런타임: 2026-07-28 live smoke에서 FastEmbed 모델과 Chroma를 거쳐
  `company-policy.txt` citation을 확인했다.
