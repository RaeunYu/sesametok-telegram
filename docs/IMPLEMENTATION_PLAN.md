# 구현 계획 — sesametok-telegram

인터뷰로 확정된 결정만 담습니다. 계약의 원본은 **백엔드 저장소의 `CLIENT_INTEGRATION.md`**
(`~/sesametok-backend/CLIENT_INTEGRATION.md`, 마지막 확인 커밋 `07b8e8f`)입니다.
용어는 `CONTEXT.md`를 따릅니다.

---

## 1. 확정된 제품 정의

| 항목 | 확정 |
|---|---|
| 형태 | 양방향. **수신이 코어**, 발신은 "이미 열린 창에 대한 원격 입력" |
| 사용자 | 설치 1개 = 사용자 1명 = 카카오톡 계정 1개. 멀티테넌시 없음 |
| 공간 구조 | **카카오톡 방마다 텔레그램 2인 그룹**(봇 + 본인). 봇과의 DM은 **컨트롤 패널** |
| 대상 방 | 사용자가 백엔드에서 지정한 **노출 대상 방**만 연결 가능(`/link`) |
| 과거 | 자동 push 없음. `/history`로 온디맨드 조회만 |
| 발신 전제 | 대상 방의 카카오톡 **채팅 창이 열려 있어야** 함. 클라이언트는 창을 열지 않음 |
| 포커스 | **절대 빼앗지 않는다** — `background_only: true` 고정 |
| `draft` | **사용하지 않음** (`background_only`와 조합 불가이기도 함) |
| `dry_run` | **사용하지 않음** (창 열림을 검증하지 못하므로 확인 단계로 무의미) |
| 에코 | `origin_client_request_id`가 채워진 이벤트는 전달하지 않음 |
| 발신자 표기 | **모든 방에서** `[카카오톡 사용자명]: 본문` (1:1 방에서도 유지) |
| 실행 | 단일 Python 프로세스: SSE 구독 + 텔레그램 long polling + 직렬 발신 |
| 스택 | Python 3.13 + uv, **Bot API 직접 구현**(stdlib), 가짜 백엔드 스텁 테스트 |

**정직한 제품 문구**
> 수신은 카카오톡 창이 없어도 동작합니다. 발신은 대상 방 창이 열려 있어야 하며,
> 그때도 포커스를 빼앗지 않습니다.

**노출(exposed) vs 연결(link)**: 노출은 "보여줄 의사"(백엔드 단일 출처), 연결은 "그 방의
텔레그램 그룹"(클라이언트 로컬). **연결은 노출된 방에만 가능**하고, 백엔드는 텔레그램
`chat_id`를 알지 못합니다.

---

## 2. 아키텍처

```
카카오톡 방 A ──┐
카카오톡 방 B ──┼─▶ [텔레그램 그룹 A]  ← 봇 + 본인 2명
카카오톡 방 C ──┘   [텔레그램 그룹 B]

                 [봇 DM] = 컨트롤 패널(/status, /rooms, /help)
```

```
┌──────────────────────────────────────────────┐
│ sesametok-telegram (단일 asyncio 프로세스)     │
│                                              │
│  EventStream ──▶ InboundPipeline ──▶ 그룹 G   │
│   (SSE)          ├ 바인딩 조회(chat_id→G)     │
│                  ├ 에코 억제                  │
│                  └ 렌더링/분할                │
│                                              │
│  TelegramUpdate ─▶ OutboundPipeline ─▶ Backend│
│   (long poll)     ├ 그룹 G → 카톡 방          │
│                   ├ 발신(background_only)     │
│                   └ 결과 → 그룹에 상태 메시지  │
│                                              │
│  Store(sqlite) ← 바인딩·전달기록·커서          │
└──────────────────────────────────────────────┘
```

### 모듈 경계

| 모듈 | 책임 | 공개 인터페이스(요약) |
|---|---|---|
| `config.py` | 설정 로딩·검증·경로(0600) | `Settings.load()` |
| `backend/client.py` | 백엔드 HTTP, 오류 → 예외 매핑 | `health()`, `status()`, `rooms(exposed_only)`, `room(chat_id)`, `messages(chat_id, limit, before)`, `send(chat_id, text, request_id)`, `set_exposed(chat_id, exposed)` |
| `backend/stream.py` | SSE 구독·재연결·프레임 파싱·갭 감지 | `async for event in stream()` |
| `store.py` | sqlite3 스키마·쿼리 | `bind()`, `unbind()`, `chat_of_room()`, `room_of_chat()`, `record_delivery()`, `cursor()/set_cursor()`, `upsert_rooms()` |
| `bridge/inbound.py` | 이벤트 → 해당 그룹 전달 | `handle(event)` |
| `bridge/outbound.py` | 그룹 메시지 → 발신 | `handle_update(update)` |
| `bridge/render.py` | `Message` → 텔레그램 텍스트 | `render(message)`, `split(text)` — **순수 함수** |
| `bridge/binding.py` | 바인딩 상태·`/link` 대화 | `pending()`, `confirm()`, `unlink()` |
| `telegram/bot.py` | long polling, send/edit, 인라인 키보드 | `run()`, `send()`, `edit()`, `answer_callback()` |
| `telegram/commands.py` | 명령 파서·응답 | 순수 매핑 + 핸들러 |
| `cli.py` | `setup`, `run`, `status` | — |

**원칙**: 렌더링·명령 파싱·오류 매핑은 **순수 함수**로 분리해 텔레그램 없이 테스트합니다.

---

## 3. 저장소 스키마 (sqlite3, WAL)

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- schema_version, last_seq, last_update_id

CREATE TABLE rooms (            -- 백엔드 Room 캐시
  chat_id TEXT PRIMARY KEY,
  chat_name TEXT NOT NULL,
  chat_type TEXT NOT NULL,
  exposed INTEGER NOT NULL DEFAULT 0,
  last_message_at TEXT,
  message_count INTEGER
);

CREATE TABLE bindings (         -- 텔레그램 그룹 ↔ 카카오톡 방
  telegram_chat_id INTEGER PRIMARY KEY,
  kakao_chat_id TEXT NOT NULL UNIQUE,
  linked_at TEXT NOT NULL
);

CREATE TABLE deliveries (       -- 전달 기록(수정·중복 방지)
  telegram_chat_id INTEGER NOT NULL,
  telegram_message_id INTEGER NOT NULL,
  kakao_chat_id TEXT NOT NULL,
  kakao_message_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (telegram_chat_id, telegram_message_id)
);
CREATE INDEX deliveries_kakao ON deliveries(kakao_chat_id, kakao_message_id);
```

**본문은 저장하지 않습니다.** 발신 텍스트는 **메모리에만**(재시도 버튼용, 10분 만료) 두고,
프로세스 재시작 시 사라집니다. 로그에는 개수·id·문자 수·상태만 남깁니다.

---

## 4. 수신 파이프라인

1. 기동 시 `GET /v1/status`의 `archive.event_seq`를 **헤드**로 저장 (과거 재생 없음).
2. `GET /v1/events?since=<head>` 구독.
3. 처리 순서: **갭 감지 → 종류 분기 → 바인딩 조회 → 에코 억제 → 렌더 → 해당 그룹 전송 → 기록**.
4. `message.created`: 그 방이 **바인딩되어 있으면** 해당 그룹으로, 없으면 조용히 버림.
5. `message.updated`: `deliveries`로 텔레그램 메시지를 찾아 **edit**. 없으면 신규 전송으로 폴백.
6. `room.updated`: 방 캐시·노출 상태 갱신. **노출이 해제된 방의 바인딩은 유지하되 전달을 중단**하고,
   그 그룹에 **1회** 안내(상태 플래그로 반복 방지).
7. `send.result`: 같은 `client_request_id`의 상태 메시지가 있으면 결과 반영(보조 신호).

**갭 감지**: `seq != last_seq + 1`이면 ① **연결을 끊고 `Last-Event-ID=last_seq`로 재접속**한다 —
서버가 아카이브에서 재생하므로 끊긴 구간이 그대로 메워진다(라이브 확인: `?since=0`이 6,041개를
seq 연속으로 재생). ② `409 event_replay_unavailable`(prune 이후)일 때만 바인딩된 방마다
`GET /v1/rooms/{id}/messages?since=<방 커서>`로 REST 보정하고 헤드에서 재구독한다.

**에코 억제**: `origin_client_request_id`가 **null이 아니면 전달하지 않음**.
(백엔드는 이 필드를 이 백엔드의 발신 요청에만 채우므로, 재시작으로 내 요청 id 집합을 잃어도 안전.)

**연결 끊김**: 지수 백오프(1s→30s) 재접속, 상태 전환(끊김/복구)만 1회 통지.

---

## 5. 발신 파이프라인

```
텔레그램 그룹 G의 평문
   → bindings에서 카톡 방 조회 (없으면 무시)
   → «그룹»에 상태 메시지: ⏳ 전송 중
   → POST /v1/messages {chat_id, text, mode:"send",
                        accept_use_policy:true, background_only:true,
                        client_request_id:<새 UUID>}
   → 상태 메시지 edit:
       sent            → ✅ 전송됨
       unconfirmed     → ⚠️ 전달 여부 미확인 — 재시도 금지(버튼 없음)
       window_not_open → 🚪 창이 닫혀 있음 + [다시 보내기](새 UUID)
       rate_limited    → ⏳ 잠시 후 + [다시 보내기]
       그 외           → ❌ 실패(code)
```

- **동시 발신 1건**(백엔드 단일 워커). 클라이언트도 직렬 큐.
- 20,000자 초과는 클라이언트에서 먼저 거부.
- `retryable` 필드는 **무시**(429/`unconfirmed`도 `false`).
- `duplicate_request`(200)는 성공 처리하되 `room`/`chars`는 신뢰하지 않음.
- 텔레그램 4,096자 제한 → 분할 전송.

---

## 6. 렌더링 규칙

```
[홍길동]: 안녕하세요
```

| 조건 | 처리 |
|---|---|
| 항상 | **`[발신자명]: 본문`** — 1:1 방에서도 접두 유지(A17). 그룹 제목이 곧 방 이름이므로 방 헤더 없음 |
| `is_feed == true` | 본문 출력 금지(원본 JSON일 수 있음) → `⚙️ 시스템 메시지` |
| `is_placeholder == true` | `⚠️ 미지원 메시지 (type_<N>)` |
| `text`가 공백뿐 | `(빈 메시지)` |
| `message.updated` | edit, 실패 시 신규 전송 |
| 그 외 | plain text (Markdown 미사용 → 이스케이프 사고 방지) |

---

## 7. 명령어

**그룹 채팅**(privacy off 전제)

| 명령 | 동작 |
|---|---|
| `/link` | 이 그룹을 노출 방 중 하나에 연결(인라인 키보드, 이미 연결된 방 제외) |
| `/unlink` | 이 그룹의 연결 해제 |
| `/history [n]` | 이 그룹의 방 최근 n건(기본 20) + `📜 더 보기` |
| `/status` | 연결 상태·백엔드 헬스·마지막 seq |
| `/help` | 위 목록 + "그룹은 비공개로 유지" 경고 |

**봇 DM(컨트롤 패널)**: `/start`(화이트리스트 확인), `/rooms`(노출 방 + 연결 현황),
`/status`, `/help`. DM에서는 발신하지 않습니다.

비노출 방은 목록·검색·`/history` 어디에도 나타나지 않습니다.

---

## 8. 온보딩·보안

`uv run sesametok-telegram setup`:
1. 봇 토큰 입력(되비추지 않음) → `~/.config/sesametok-telegram/config.toml`(0600)
2. **BotFather에서 `/setprivacy → Disable`** 안내 (그룹 추가 **전에** 해야 재초대가 필요 없음)
3. 봇에게 `/start` → 받은 `user_id`를 확인받아 화이트리스트에 기록
4. 백엔드 토큰 경로 확인(기본 `~/Library/Application Support/sesametok-backend/api_token`)
5. "자동 시작(LaunchAgent) 등록?" 선택

**보안 불변식**
- 그룹은 **비공개 유지**. 공개 링크(username)를 만들지 않는다 — 일반 그룹은 공개 자체가 불가.
- 초대 링크를 외부에 공유하지 않는다.
- 바인딩된 `chat_id`와 소유자 `user_id` 외의 업데이트는 **저장도 응답도 하지 않는다**.
- 바인딩된 그룹은 주기적으로 `getChatMemberCount`로 확인하고, **2명 초과면 그 그룹에 1회 경고**.
- 토큰은 로그·오류·`/status`에 절대 출력하지 않는다.

---

## 9. 마일스톤

| # | 내용 | 완료 기준 | 상태 |
|---|---|---|---|
| **M0** | uv 프로젝트, `config.py`, 로깅(본문 마스킹), CLI 골격, `.gitignore` | `uv run sesametok-telegram --help` | ✅ 완료 (2026-11-07) |
| **M1** | `backend/client.py` + 가짜 백엔드 스텁 + 계약 테스트 | 스텁 대상 전 엔드포인트 테스트 통과 | ✅ 완료 (2026-11-07, 29 passed) |
| **M2** | `store.py` + `backend/stream.py` + 갭 감지 + 재연결 | 재연결·갭 시나리오 테스트 통과 | ✅ 완료 (2026-11-07, 50 passed) |
| **M3** | `render.py` + 인바운드 파이프라인 + 바인딩(`/link`) + 그룹 전송 | 노출·연결된 방만 도착, 미연결·에코는 무시 | ✅ 완료 (2026-11-07, 95 passed) |
| **M4** | 발신 파이프라인 + 실행 루프(SSE·long polling) + 상태 메시지 + 오류 매핑 | 오류 코드별 상태 메시지 테스트 + 첫 라이브 수신/발신 확인 | 대기 |
| **M5** | `setup` CLI, LaunchAgent, DM 컨트롤 패널, README | 문서만 보고 설치·동작 | 대기 |

**수동 스모크(마지막)**: **실제 발신 테스트는 사용자에게 먼저 묻는다** — 어느 방에, 어떤 본문으로
보내도 되는지 확인을 받은 뒤에만 실행한다. 사용자가 그 방의 창을 열어둔 상태에서 `.......` 같은
무해한 문자열 1건 → `sent` 확인 → 몇 초 뒤 에코가 **전달되지 않는지** 확인. `unconfirmed`면
**재시도 금지**하고 결과를 보고한다.

---

## 10. 테스트 계획

**자동(스텁 기반)**
1. `?exposed=0` → `400`(전체는 생략만) — 파라미터를 잘못 보내지 않는지.
2. `PATCH`가 같은 값이어도 `room.updated`를 매번 발행 → 중복 이벤트를 무해하게 처리.
3. `unconfirmed`의 **두 형태**(200+`result`, 409+`error.code`)를 동일하게 처리.
4. `duplicate_request`(200, `room:null`) → 성공 처리, `chars` 무시.
5. SSE: `": ping"` 무시, `Last-Event-ID` 재접속, `409 event_replay_unavailable` → 헤드 재구독.
6. 갭: `seq` 점프 시 REST 보정이 실행되고 중복 전달이 없다.
7. 에코: `origin_client_request_id`가 있으면 전달 안 됨.
8. 라우팅: 미바인딩 방 이벤트는 어디로도 가지 않고 저장되지 않는다.
9. 렌더링: 접두 유지, `is_feed`/`is_placeholder`/빈 본문/4,096자 분할.
10. 오류 매핑: `window_not_open`→재시도 버튼, `unconfirmed`→버튼 없음, `rate_limited`→백오프.
11. 보안: 화이트리스트 밖 `user_id`·미바인딩 `chat_id`의 업데이트는 무시된다.

**수동**
- 실제 백엔드 + 실제 카카오톡 창에서 발신 1건.
- 메뉴바에서 노출 토글 → `/rooms` 반영 확인.

---

## 11. 리스크와 대응

| 리스크 | 대응 |
|---|---|
| 발신이 실제로 `sent`까지 가는지 미검증(백엔드도 실기 미검증) | M4 후 수동 스모크 1건. 실패 시 문구 유지 + `/status` 안내 |
| `unconfirmed`가 조용히 사라짐 | 발신 결과 메시지에만 표시, `/status`에 최근 결과 요약 |
| SSE 큐 유실(2,000) | 갭 감지 + 방별 `since` 보정 |
| 텔레그램 rate limit | 초당 1건/챗 + 전역 토큰 버킷 |
| 그룹이 공개로 바뀌어 노출 | `setup`/`/help` 경고, 멤버 수 감시 |
| 봇 토큰 유출 | 0600, 로그 금지, 유출 시 BotFather에서 재발급 안내 |
| privacy off 반영 안 됨(그룹 추가 후 변경) | `setup`이 순서를 강제(먼저 `/setprivacy`) |
| 개인정보가 로그/에러에 남음 | 본문 미저장·미로깅, 예외 메시지에서 본문 제거 테스트 |

---

## 12. 결정 기록 후보 (ADR)

1. **발신은 포커스를 빼앗지 않는다 — `background_only: true` 강제**
2. **`dry_run` 선행 확인 UX를 채택하지 않는다** — 창 열림을 검증하지 못함
3. **방마다 2인 텔레그램 그룹** — 슈퍼그룹 토픽(b)과 단일 DM(a)을 기각한 이유,
   Bot API가 그룹을 만들 수 없다는 제약, privacy mode의 위치
