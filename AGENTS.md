# AGENTS.md — sesametok-telegram

카카오톡 아카이브를 텔레그램으로 옮기고, 텔레그램에 쓴 텍스트를 카카오톡으로 보내는 **메신저
어댑터**다. 카카오톡 읽기·쓰기는 전부 백엔드(`~/sesametok-backend`)가 하고, 이 저장소는 loopback
HTTP/SSE만 소비한다.

- **계약 원본**: `~/sesametok-backend/CLIENT_INTEGRATION.md` (이 저장소에는 사본이 없다)
- **용어**: `CONTEXT.md` — `방`, `노출 대상 방`, `연결`, `에코`, `미확인`, `창 열림`
- **설계·마일스톤**: `docs/IMPLEMENTATION_PLAN.md`
- **되돌리기 어려운 결정**: `docs/adr/0001`(포커스 불변식), `0002`(`dry_run` 미채택), `0003`(방마다 2인 그룹)

## 작업 순서

1. **계약 확인** — API를 건드리기 전에 백엔드 `CLIENT_INTEGRATION.md`의 해당 절을 읽는다. 문서와
   실제 동작이 어긋나면 사용자에게 보고한다. 이 저장소는 백엔드 코드를 고치지 않는다 — 백엔드
   수정이 필요하면 `docs/backend-prompts/`에 작업 지시서를 쓴다.
2. **결정은 사용자 몫** — 확정되지 않은 선택지를 만나면 추천안과 근거를 붙여 묻는다. 답을 받기
   전에는 구현하지 않고, 질문에 번호가 붙은 답이 오지 않으면 확정으로 처리하지 않는다.
3. **구현** — 판단 로직(렌더링·라우팅·오류 매핑)은 **순수 함수**로 분리해 텔레그램·네트워크 없이
   테스트한다. 루프와 I/O는 그 주위에 얇게 둔다.
4. **검증** — `./.venv/bin/python -m pytest -q`가 전부 green이어야 보고한다. 계약을 다루는
   변경이면 라이브 백엔드에 **읽기 호출**로도 확인한다.
5. **보고** — 무엇을 했는지, 테스트 수, 남은 위험, **1줄 커밋 메시지 제안**을 함께 적는다.

## 하드 룰

- **커밋은 사용자가 한다.** 에이전트는 변경을 작업 트리에 남기고 커밋 메시지만 제안한다.
- **`README.md`는 사용자가 요청할 때만 쓴다.**
- **실제 발신 테스트는 먼저 묻는다** — 어느 방에 어떤 본문으로 보내도 되는지 확인받고 1건만 보낸다.
- **로그·문서·커밋에 남기는 것**: 개수·id·상태·문자 수. 메시지 본문·방 이름·닉네임·토큰·키는
  넣지 않는다. 예외 메시지에도 본문을 넣지 않는다.
- **발신 파라미터**: `mode: "send"`, `background_only: true`, `accept_use_policy: true`,
  요청마다 새 `client_request_id`. 포커스를 빼앗지 않는 것이 제품 불변식이다(ADR-0001).
- **`draft`·`dry_run`은 쓰지 않는다**(ADR-0002). 발신은 즉시 보내고 결과를 상태 메시지로 보여준다.
- **에코 억제**: `origin_client_request_id`가 null이 아니면 전달하지 않는다. 텍스트·시간창 추측은
  정상 메시지를 놓친다.
- **노출(exposed)은 백엔드 소유, 연결(binding)은 클라이언트 소유.** 백엔드는 텔레그램 `chat_id`를
  모르고, 클라이언트는 `PATCH /v1/rooms/{chat_id}`로 노출을 바꾼다.
- **의존성 0 유지**: 표준 라이브러리만 쓴다. 텔레그램은 Bot API를 직접 호출한다(pyproject의
  런타임 의존성은 비어 있다).

## 함정 (계약에서 실제로 확인한 것)

- `GET /v1/rooms`의 `exposed`는 `1`/`true`만 유효하다. 전체는 **파라미터 생략**. `exposed=0`은 400.
- `PATCH /v1/rooms/{chat_id}`는 멱등이지만 **같은 값이어도 매번 `room.updated`를 발행**하고 seq를 쓴다.
- `unconfirmed`는 **200**(`result: "unconfirmed"`)과 **409**(`error.code`) 두 형태로 온다.
  어느 쪽이든 **재시도 금지**.
- `duplicate_request`는 200이고 `room`/`chars`가 무의미하다.
- `retryable` 필드를 재시도 판단에 쓰지 않는다(429·`unconfirmed`도 false다).
- 시각은 `+00:00`, 초 단위. `Z`나 `+09:00`을 보내면 문자열 비교가 깨져 **조용히 빈 결과**가 나온다.
- SSE는 Bearer 토큰이 필요하고 브라우저 `EventSource`로는 붙지 않는다. `": ping"`은 무시한다.
- SSE `seq` 갭은 **재접속 재생**으로 메운다(서버가 아카이브에서 재생). REST 보정은
  `409 event_replay_unavailable`(prune 이후)일 때만.
- 텔레그램 `chat.id`는 음수다. 수신 라우팅은 오직 `bindings` 테이블로만 판단하고, 그 밖의
  `chat_id`·`user_id`는 저장도 응답도 하지 않는다.

## 명령

```bash
UV_CACHE_DIR="$PWD/.uv-cache" uv sync          # 런타임 의존성 0, dev는 pytest
./.venv/bin/python -m pytest -q
./.venv/bin/sesametok-telegram status          # 비밀값 없이 설정 요약
```

`uv`가 `~/.cache/uv`에 쓰지 못하는 샌드박스가 있어 캐시를 저장소 안(`.uv-cache/`, gitignore)으로
돌린다. 네트워크가 없으면 `pytest`만 실행해도 된다(의존성 0).
