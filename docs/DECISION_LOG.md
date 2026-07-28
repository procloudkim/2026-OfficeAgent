# Decision Log

평가자가 선택 이유와 구현 증거를 빠르게 대조하기 위한 변경 이력입니다. ADR은
되돌리기 어려운 결정에 사용하고, 이 표는 각 결정을 Why–What–How와 trade-off로
요약합니다. 실행 증거가 없는 결정은 `VERIFIED`로 표시하지 않습니다.

| ID | Date | Status | Why | What | How | Trade-offs | Evidence |
|---|---|---|---|---|---|---|---|
| DEC-001 | 2026-07-28 | VERIFIED | 계층 분리와 문서-코드 대조를 쉽게 한다. | `api/application/domain/infrastructure` 구조와 일관된 이름을 채택한다. | `bootstrap.py`만 concrete adapter를 조립한다. | 초기 파일 수는 늘지만 경계와 테스트가 명확해진다. | [ADR-0001](adr/ADR-0001-repository-structure-and-naming.md), 전체 suite |
| DEC-002 | 2026-07-28 | VERIFIED | 검색과 캐시 무효화를 독립 검증한다. | SQLite metadata/cache + Chroma vectors + FastEmbed 384d. | exact 후 cosine `>=0.95`; 신규 corpus 전체, 갱신 dependency만 무효화한다. | 두 저장소를 관리하지만 각 책임과 test oracle이 명확하다. | [ADR-0002](adr/ADR-0002-storage-retrieval-and-cache.md), 실제 DB adapter tests |
| DEC-003 | 2026-07-28 | VERIFIED | 실제 SDK 호출과 관찰 가능한 streaming을 만족한다. | Codex App Server stdio JSON-RPC + SSE. | 첫 delta 전 transport/timeout만 1회 retry; 이후 재시도 금지. | 중단 복구보다 중복 출력·이중 사용량 방지를 우선한다. | [ADR-0003](adr/ADR-0003-codex-app-server-streaming.md), protocol/SSE tests, final live 17 delta |
| DEC-004 | 2026-07-28 | VERIFIED | 최소 2형식과 출처 위치를 단순하게 보장한다. | pypdf PDF와 UTF-8 TXT/MD, page/character offset. | page를 넘지 않는 900자/150자 overlap 청킹. | OCR·semantic chunking은 제외하지만 결정론적 재현이 쉽다. | `test_ingestion.py`, `test_parsers_and_chunking.py` |
| DEC-005 | 2026-07-28 | VERIFIED | 구독 없이 vector/cache 실체를 테스트해야 한다. | LLM/embedding만 fake, SQLite/Chroma/HTTP/parser는 real. | keyword vector와 async delta fake를 port에 주입한다. | 모델 의미 품질은 별도 live smoke와 향후 gold set이 필요하다. | `uv run pytest`, `scripts/live_smoke.py` |
| DEC-006 | 2026-07-28 | VERIFIED | container에 로컬 구독 credential을 억지로 전달하지 않는다. | `uv run officeagent`를 공식 한 줄 실행 경로로 사용한다. | lockfile 동기화 후 host Codex login을 재사용한다. | Docker 격리는 포기하지만 PRD 허용 경로와 실제 SDK 조건을 단순히 만족한다. | console script, server/health smoke |
