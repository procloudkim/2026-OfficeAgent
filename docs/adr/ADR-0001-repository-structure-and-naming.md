# ADR-0001: 저장소 구조와 네이밍

| 항목 | 내용 |
|---|---|
| Status | Accepted — implemented |
| Date | 2026-07-28 |

## Why

평가자는 계층 분리, 구현과 문서의 일치, 자동화 테스트를 함께 확인한다. 기능이
늘어도 HTTP·유스케이스·저장소 코드가 서로 섞이지 않아야 한다.

## What

다음의 단일 Python 패키지 구조를 사용한다.

```text
src/officeagent/
├── api/                 # FastAPI routes, request/response schemas
├── application/         # ingest/query use cases and orchestration
├── domain/              # entities and repository/provider protocols
├── infrastructure/      # SQLite, Chroma, FastEmbed, Codex adapters
├── bootstrap.py         # concrete dependency composition
└── main.py              # server entrypoint
tests/                   # flat, behavior-named test modules
docs/
├── adr/
└── DECISION_LOG.md
```

루트의 제출 문서는 `README.md`, `ARCHITECTURE.md`, `PROMPT_DESIGN.md`로
유지한다. `retros/`는 Retrobot이 만드는 작업 과정 증거이며 ADR의 저장소가
아니다.

## How

- 디렉터리·모듈·함수·JSON·DB 필드는 `snake_case`를 쓴다.
- service·adapter·protocol 클래스는 `PascalCase`와 역할 접미사를 쓴다.
  예: `IngestionService`, `SqliteMetadataStore`, `CodexAppServerGateway`.
  `Chunk`, `Citation` 같은 domain value object는 역할 자체가 이름이다.
- 프로토콜은 역할명으로 쓴다. 예: `MetadataStore`, `EmbeddingProvider`,
  `LlmGateway`.
- 테스트 파일은 `test_<behavior>.py`, 함수는 기대 결과와 조건이 드러나는
  `test_<behavior>`를 쓴다. 조건 구분이 필요하면 `_when_`을 사용한다.
- API는 `/v1/documents`, `/v1/questions`, `/v1/questions/stream`을 사용한다.
  SSE 이벤트명은 `answer.started`, `answer.delta`, `answer.citation`,
  `answer.completed`, `answer.error`로 고정한다.
- ADR 파일명은 `ADR-0001-short-kebab-title.md`처럼 증가하는 4자리 번호와
  kebab-case를 사용한다. 일상적인 선택은 `docs/DECISION_LOG.md`에 남긴다.

## Trade-offs

초기 파일 수가 늘지만, 한 기능의 API·유스케이스·인프라 의존성을 분리해 테스트와
면접 설명이 쉬워진다. 모든 작은 리팩터링에 ADR을 만들지 않고, 되돌리기 어려운
기술·계약 변경에만 ADR을 만든다.

## Evidence

- 규칙: `REQ-04`, `REQ-08`, `DOC-01`, `DOC-02`, `DOCSYNC-01`.
- 구현: `src/officeagent/{api,application,domain,infrastructure}`와
  `bootstrap.py`.
- 검증: `tests/test_ingestion.py`, `test_retrieval.py`,
  `test_cache_invalidation.py`, `test_streaming.py`.
