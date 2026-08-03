# ADR-0005: Codex structured output의 incremental plaintext projection

| 항목 | 내용 |
|---|---|
| Status | `ACCEPTED` on V2 branch; `main` merge는 별도 승인 대상 |
| Date | 2026-08-03 |
| Decision | `DEC-013`; `DEC-012`의 plain-output 보류 결정을 supersede |
| Scope | `REQ-08`, `REQ-09`, `REQ-10`, `REQ-13`, `REQ-14` |

## Context

기존 gateway는 Codex의 임의 plain-text delta를 그대로 SSE에 전달했습니다. timeout,
retry와 no-tool 경계는 있었지만 최종 답변의 형태를 기계적으로 검증하지 못했습니다.
반대로 turn이 끝날 때까지 JSON 전체를 buffer하면 실제 streaming 요구를 약화시킵니다.

[OpenAI Codex App Server 문서](https://developers.openai.com/codex/app-server/)는
`turn/start.outputSchema`가 해당 turn의 최종 assistant message를 제약하고,
agent-message delta notification을 streaming한다고 설명합니다. 설치된
`codex-cli 0.146.0`의 생성 protocol schema에서도 `outputSchema` field를 확인했습니다.

## Decision

1. `turn/start.outputSchema`에 정확히 한 required string property `answer`만 허용하는
   object schema를 보냅니다.
2. 빈 delta는 item을 bind하지 않고 무시합니다. 첫 non-empty matching delta는
   non-empty `itemId`가 없으면 실패하고, 유효하면 bind합니다. 이후 누락·변경은
   무시하지 않고 protocol error로 종료하며 JSON string을 provisional plaintext로
   incremental projection합니다.
3. completion에서 전체 raw JSON을 strict하게 다시 parse하고 정확한 field 집합,
   type, nonblank, duplicate key 부재와 incremental 결과 일치를 확인합니다.
4. malformed/incomplete/blank, NUL, invalid escape/surrogate, 추가·중복 key와 trailing
   data는 protocol error입니다. retry, citation, completed event와 cache write로
   진행하지 않습니다.
5. transport/timeout은 gateway가 application에 공백 포함 첫 non-empty decoded
   fragment를 yield하기 전까지만 한 번 재시도합니다. answer 이전 JSON framing은
   visible output이 아닙니다. 이 cutoff는 client 수신이나 exactly-once를 보장하지 않습니다.
6. 최초 answer와 cache replay는 동일한 문자열을 보존합니다.

## Bounds

| 경계 | 로컬 정책 | 이유 |
|---|---:|---|
| decoded answer | 65,536 Python `str` code points | 사용자 응답·cache 크기를 제한 |
| raw structured JSON | 1,048,576 Python `str` code points | parser 누적 입력을 제한 |
| App Server stdout line | 8 MiB | outer JSON-RPC encoding을 포함한 bounded transport |

[RFC 8259](https://www.rfc-editor.org/rfc/rfc8259)은 JSON object member name의
유일성을 권고하며 parser가 수용 크기에 제한을 둘 수 있음을 설명합니다. 위 숫자는
RFC나 Codex의 보장이 아니라 이 제품의 명시적 정책입니다. raw/answer buffer는
`StringIO`로 누적하고 key는 `answer`와 즉시 비교해 delta 수나 잘못된 긴 key에 따른
reference 증폭을 피합니다.

## Failure semantics

SSE `answer.delta`는 provisional입니다. client는 `answer.completed`를 받아야 성공
답변으로 확정할 수 있습니다. 이미 전송한 일부 text는 후속 protocol 오류가
발생해도 회수할 수 없습니다. 이 경로의 fail-closed 의미는 다음과 같이 제한합니다.

- 마지막 event는 `answer.error`
- `answer.citation`과 `answer.completed` 없음
- JSON endpoint는 sanitized `502`
- answer cache write 없음

정상 turn completion 뒤 session cleanup 오류는 검증된 답변을 바꾸지 않고 내부
warning으로 기록합니다. cleanup은 `ProcessLookupError` race를 허용하며 원래
timeout/retry/protocol 결과를 가리지 않습니다.

## Verification

- protocol: exact schema, 모든 split point와 1-character fragment differential,
  escape/Unicode, item binding, zero/blank, duplicate/additional/wrong key/type,
  NUL과 trailing/incomplete
- bounds: answer/raw 각각 max와 max+1, subprocess의 명시적 8 MiB line setting
- retry: prefix-only timeout retry, visible timeout/OSError no-retry, cleanup error isolation
- cross-layer: partial delta 뒤 SSE error, JSON 502, no citation/completed/cache
- live: actual Codex 0.146.0 + Uvicorn HTTP/SSE의 두 cache miss에서 복수 plaintext delta;
  sanitized receipt는 본문 equality가 아니라 전이·delta count·citation만 증명

실행 횟수와 current-SHA 영수증은
[V2 Traceability](../V2_REQUIREMENTS_TRACEABILITY.md)가 소유합니다.

## Consequences and non-goals

- 기존 plaintext SSE client 계약은 유지하지만 custom incremental parser의 test
  surface가 늘어납니다.
- schema는 출력 모양을 제한할 뿐, answer factuality나 citation entailment를
  보장하지 않습니다.
- tokenizer 기반 chunk/answer 최적화, latency 개선 주장과 의미 품질 benchmark는
  이 결정에 포함하지 않습니다.
