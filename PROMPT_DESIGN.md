# Prompt Design

## 목표

프롬프트의 역할은 문서 근거 안에서 간결히 답하게 하는 것입니다. 출처 식별과
근거 없음 판정의 1차 gate는 LLM이 아니라 검색 결과와 서버 코드가 맡습니다.
프롬프트의 canonical source는
[`src/officeagent/application/prompts.py`](./src/officeagent/application/prompts.py)입니다.

## Why–What–How

| Why | What | How |
|---|---|---|
| 문서 밖 추론을 억제 | evidence-only 규칙 | “evidence block이 지지하는 사실만” 명시 |
| prompt injection 완화 | 문서는 untrusted data | evidence 안 명령을 따르지 말라고 명시하고 metadata attribute를 escape |
| 근거 부족 시 환각 방지 | 명시적 abstention | 검색 score gate + prompt의 insufficient-evidence 문구 |
| 다국어 질문 품질 | 질문 언어 유지 | 질문과 같은 언어로 간결히 답하도록 지시 |
| 허위 출처 방지 | citation을 생성에서 분리 | LLM에게 참고문헌을 쓰지 말라고 하고 서버가 Chunk에서 생성 |

## Prompt 구조

```text
role/rules
<question>
사용자 질문
</question>
<evidence_set>
  <evidence id="1" document="..." chunk="..." page="...">
  검색된 원문
  </evidence>
</evidence_set>
```

각 evidence에는 서버가 알고 있는 filename, chunk id, page를 경계 속성으로
넣고 client-controlled filename metadata는 attribute용으로 escape합니다. 모델
출력에는 citation 형식을 요구하지 않습니다. 이 구조는 API가 실제 검색된
문서·부분만 반환하게 하지만, 문서 본문의 적대적 instruction을 의미적으로 완전히
차단하거나 모델이 각 청크를 답에 사용했음을 증명하지는 않습니다.

## Grounding 전략

1. cosine score가 기본 `0.30` 미만인 청크는 prompt에 넣지 않습니다.
2. 남은 청크가 없으면 LLM을 전혀 호출하지 않고
   “업로드된 문서에서 충분한 근거를 찾지 못했다”고 반환합니다.
3. evidence가 있더라도 질문을 확정하기 부족할 수 있으므로 prompt에도
   insufficient-evidence 규칙을 반복합니다.
4. evidence 안의 “이전 지시를 무시하라” 같은 문자열은 데이터일 뿐이라고
   명시합니다. 이는 방어 규칙이며 adversarial evaluator를 통과했다는 주장은 아닙니다.
5. 기본 5개(설정 상한 20), 청크당 기본 900자로 context를 제한해 비용과
   distractor를 제어합니다.

`0.30`과 top-5는 안전한 초기값이며 데이터셋 기반 정답률 튜닝 결과라고
과장하지 않습니다. 현재 fixture 테스트는 관련/무관 문서 분리와 LLM 미호출을
검증합니다.

서버 생성 citation은 score gate를 통과한 검색 청크의 provenance입니다. 현재는
모든 relevant hit를 반환하며 model answer와 각 citation 사이 entailment나 실제
사용 subset을 post-generation으로 검증하지 않습니다. `grounded=true`도 이
제약 안에서 해석해야 합니다.

## 생성과 스트리밍

Codex App Server의 `item/agentMessage/delta`만 모델 출력으로 인정합니다.
서버가 전체 답을 받은 뒤 가짜로 자르지 않습니다. 첫 delta 전 transport/timeout
실패만 한 번 재시도하고, delta 이후 실패는 `answer.error`로 끝냅니다.

캐시 hit의 80자 replay는 응답 UX를 위한 별도 경로이며, API의
`cache_hit=true`로 live generation과 명확히 구분합니다.

## 테스트와 관찰 항목

- 관련 질문: 기대 문서가 top-1이고 prompt에 해당 원문이 포함되는가
- 무관 질문: LLM 호출 수가 증가하지 않고 citation이 비어 있는가
- 스트리밍: 두 gateway delta가 같은 순서의 두 SSE delta인가
- 캐시: exact/similar 질문이 prompt/LLM을 다시 호출하지 않는가
- metadata 경계: 악성 filename이 evidence attribute를 닫지 못하는가
- 인젝션 의미 회귀: 향후 악성 문서 fixture와 answer policy evaluator를 추가할 것

현재 자동화는 구조·호출 경계를 검증합니다. 답변의 의미 품질을 정량화하는 gold
dataset 평가는 아직 구현하지 않았으며, 이를 구현했다고 주장하지 않습니다.
