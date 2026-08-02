# ADR-0003: Codex App Server와 SSE 스트리밍

> 증거 범위: V1의 실제 Codex smoke와 V2의 current-SHA live incremental SSE는
> 각각 `VERIFIED`입니다. V2의 변동 evidence는
> [V2 Traceability](../V2_REQUIREMENTS_TRACEABILITY.md)만 소유합니다.

| 항목 | 내용 |
|---|---|
| Decision Status | `Accepted` |
| Evidence Status | V1 historic live와 V2 current live 각각 `VERIFIED` |
| Date | 2026-07-28 |

## Why

과제는 선택한 구독 기반 SDK/CLI를 서버 코드에서 호출하고, 완성된 답변을 한 번에
반환하지 않는 실제 스트리밍을 요구한다. 테스트는 구독 없이 재현 가능해야 한다.

## What

- LLM 어댑터는 Codex App Server를 stdio JSON-RPC로 호출한다.
- 서버는 `/v1/questions/stream`에서 실제 도착 순서의 delta를 SSE로 전달한다.
- 이벤트 계약은 `answer.started` → 하나 이상의 `answer.delta` →
  `answer.citation` → `answer.completed`이며, 실패 시 `answer.error`다.
- 첫 delta 전에는 제한된 재시도를 허용하되, 첫 delta 뒤에는 재시도하지 않는다.
- 테스트는 구독이나 로컬 Codex 실행 없이 fake `LlmGateway`로 스트리밍·오류·캐시
  경로를 검증한다.

## How

- `CodexAppServerGateway`는 JSON-RPC 요청/응답과 스트림 알림을 `LlmGateway`
  프로토콜 뒤에 숨긴다.
- API 계층은 각 SSE data에 `request_id`를 포함한다. `answer.citation`은
  source payload를, 시작·완료 이벤트는 cache metadata를 포함한다.
- retry는 첫 사용자 가시 delta 전에만 timeout/일시 오류에 적용한다. 이미 보낸
  출력을 다시 생성하지 않아 중복 답변·이중 비용을 막는다.
- `initialize` 뒤 `initialized` notification을 보내고, ephemeral/read-only/
  never-approve thread에서 tool 금지 base instruction을 사용한다.
- 실런타임에서 experimental capability 없이 `environments=[]`가 거절됨을
  발견했다. `experimentalApi=true`로 명시적 opt-in한 뒤
  `environments=[]`/`dynamicTools=[]`를 보내 environment/dynamic tool을
  비활성화한다.
- Codex cwd는 문서/소스가 없는 전용 디렉터리이며 command·file·MCP·web 등
  tool item 시작을 오류로 처리한다. 동시 process는 2개, notification queue는
  100개로 제한한다.
- 실제 런타임 검증과 fake 테스트 검증을 분리해 기록한다.

## Trade-offs

stdio JSON-RPC 어댑터는 HTTP SDK보다 구현량이 있지만, API 키 없이 사용 가능한
Codex CLI 경로와 맞는다. delta 이후 retry를 포기하면 일부 중단 응답을 복구하지
못하지만, 이미 전송한 SSE의 순서·비용·정합성을 보존한다.

## Evidence

- 규칙: `REQ-08`, `TECH-03`, `TECH-06`, `TECH-08`, `TECH-09`, `TECH-10`.
- 자동화: `tests/test_codex_protocol.py`가 protocol 순서, ID filtering,
  zero-delta, failed turn, retry 전후 경계를 검증한다.
- API: `tests/test_streaming.py`가 두 gateway delta를 두 SSE delta로 전달함을
  검증한다.
- 실런타임: 2026-07-28 `scripts/live_smoke.py`에서 Codex CLI 0.145.0을 통해
  실제 Uvicorn HTTP SSE 최종 delta 17개, pre-completion delta, completed,
  `company-policy.txt` citation을 확인했다.
- V2 실런타임: current-SHA `scripts.live_smoke`에서 Codex CLI 0.146.0,
  Uvicorn HTTP/SSE, 두 miss의 복수 delta, exact cache replay, document version
  update 뒤 miss와 versioned citation을 확인했다.
