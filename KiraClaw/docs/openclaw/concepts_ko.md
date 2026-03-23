# OpenClaw Concepts

이 문서는 OpenClaw의 핵심 개념을 제품 내부 관점에서 정리한 문서다. 공식 문서 전반에 흩어진 개념을 하나의 흐름으로 요약해, OpenClaw를 "무엇이 중심인 시스템인지" 기준으로 이해할 수 있게 하는 것이 목적이다.

## 한 줄 정의

OpenClaw는 **항상 켜져 있는 Gateway 위에서 agent, tools, channels, nodes, background work를 운영하는 gateway-centered agent system**이다.

핵심은 이 세 가지다.

- `Gateway`가 중심 daemon이다.
- client와 node는 모두 Gateway에 붙는다.
- LLM 호출은 Gateway runtime 안의 한 subsystem이다.

## 핵심 구성 요소

### Gateway

OpenClaw의 중심은 `Gateway`다.

- long-running daemon/service다.
- typed WebSocket API를 제공한다.
- HTTP/control API도 함께 제공한다.
- channel, node, session, tools, background process를 관리한다.
- event와 logs의 source of truth 역할을 한다.

즉 OpenClaw는 "챗봇에 툴을 붙인 제품"이 아니라, **Gateway가 agent system 전체를 운영하는 구조**다.

### Clients

client는 Gateway에 붙는 control-plane client다.

예:

- mac app
- CLI
- Web UI
- automation runner

이들은 로컬 파일을 직접 만지는 주체가 아니라, Gateway에 붙어 session과 tool surface를 사용하는 주체다.

### Nodes

node는 `role: node`로 Gateway에 붙는 실행 표면이다.

node는 단순 UI가 아니라 capability provider다.

예:

- `canvas.*`
- `camera.*`
- `screen.record`
- `location.get`

즉 OpenClaw에서 node는 "다른 장치"이면서 동시에 "추가 capability를 제공하는 실행 단위"다.

### WebChat

WebChat도 별도 backend가 아니라 같은 Gateway에 붙는 client다.

즉 OpenClaw는 chat UI가 먼저 있는 구조라기보다, **Gateway를 여러 client가 공유하는 구조**다.

### Agent Runtime

OpenClaw의 agent runtime은 pi를 임베드해서 사용한다.

중요한 점:

- pi를 subprocess처럼 따로 부르는 게 아니라 embedded runtime으로 쓴다.
- raw pi base tools를 그대로 노출하지 않는다.
- Gateway가 tool surface를 재구성한다.

즉 OpenClaw에서 agent runtime은 "독립 엔진"이라기보다, **Gateway 안에 들어있는 실행 코어**에 가깝다.

## Agent Loop

OpenClaw에서 agent loop는 단순 모델 호출이 아니다.

문서 기준 흐름:

- intake
- context assembly
- model inference
- tool execution
- streaming
- persistence

즉 loop는 "질문 하나에 답한다"가 아니라, **Gateway 안에서 run 하나를 authoritative하게 처리하는 실행 파이프라인**이다.

### 진입점

대표 진입점:

- `agent`
- `agent.wait`
- CLI `agent`

즉 agent run도 Gateway가 받는 요청의 한 형태다.

### 중요한 성격

#### session별 직렬화

OpenClaw는 per-session queue로 run을 직렬화한다.

즉 같은 session 안에서는 run이 동시에 충돌하지 않도록 runtime 차원에서 정리된다.

#### event stream 중심

agent loop는 내부에서 끝나지 않고 바깥으로 stream을 흘린다.

대표 stream:

- `assistant`
- `tool`
- `lifecycle`

즉 OpenClaw에서 run은 결과만 반환되는 게 아니라, **실행 상태가 control plane으로 계속 노출된다.**

#### runtime orchestration 중심

tool 호출, retries, compaction, suppression, messaging shaping 같은 것들이 loop 안에서 같이 다뤄진다.

즉 OpenClaw의 agent loop는 모델 함수가 아니라, **runtime orchestration 중심부**다.

## 세션

OpenClaw에서 session은 단순 대화 ID가 아니다.

session은 **Gateway가 소유하는 공식 상태 단위**다.

예:

- main DM session
- group/channel session
- thread/session key

그리고 이런 정책을 Gateway 설정으로 다룬다.

- `session.dmScope`
- identity links
- pruning
- reset
- maintenance

즉 session은 transport adapter가 대충 정하는 값이 아니라, **Gateway 정책과 lifecycle의 일부**다.

### 왜 중요한가

이 구조 덕분에 session은:

- transcript 관리
- context 관리
- DM 보안 격리
- reset 정책
- pruning 정책

과 연결된다.

즉 OpenClaw의 session은 대화 continuity뿐 아니라, **Gateway 운영 모델의 핵심 상태 단위**다.

## 메모리

OpenClaw memory의 source of truth는 모델 내부가 아니라 workspace 안의 파일이다.

대표 구조:

- `MEMORY.md`
- `memory/YYYY-MM-DD.md`

즉 memory는 hidden internal state가 아니라, **Gateway가 관리하는 durable workspace artifact**다.

### 중요한 개념

- durable fact는 `MEMORY.md`
- daily note는 `memory/YYYY-MM-DD.md`
- memory tool은 search/get 중심
- 필요하면 vector/hybrid retrieval과 연결
- compaction 직전에 silent memory flush turn을 돌려 durable memory를 정리할 수 있다

즉 memory는 단순 retrieval이 아니라, **session lifecycle과 연결된 저장층**이다.

## 툴 구조

OpenClaw는 tool system 자체를 제품의 큰 축으로 본다.

문서 기준 3층 구조:

- `tools`
- `skills`
- `plugins`

### tools

agent가 실제로 호출하는 typed function이다.

대표 built-in tools:

- `exec`
- `process`
- `browser`
- `web_search`
- `web_fetch`
- `read`
- `write`
- `edit`
- `apply_patch`
- `message`
- `canvas`
- `nodes`
- `cron`
- `gateway`
- `image`
- `image_generate`
- `sessions_*`

즉 shell, filesystem, web, messaging, automation, session control이 다 tool surface 안에 들어와 있다.

### skills

skills는 tool 사용법과 workflow를 설명하는 instruction layer다.

즉 OpenClaw에서 skill은 "tool의 대체물"이 아니라, **tool을 더 잘 쓰게 하는 instruction package**다.

### plugins

plugin은 channel/provider/tool/skill을 묶는 확장 패키지다.

즉 OpenClaw는 단순 tool 집합이 아니라, **plugin 가능한 tool platform** 성격이 강하다.

## tool policy

OpenClaw에서 tool은 단순 함수가 아니라 policy 대상이다.

대표 개념:

- allow / deny
- tool profile
- tool group
- provider별 tool 제한

즉 무엇을 쓸 수 있는지는 tool 구현만이 아니라, **Gateway 정책**에도 달려 있다.

## exec / process

OpenClaw의 shell 모델은 `bash` 중심이 아니라 `exec / process` 중심이다.

### exec

`exec`는 shell 작업을 시작하는 도구다.

- 짧게 끝나면 foreground 결과를 바로 준다.
- 오래 걸리면 background session으로 전환된다.
- `yieldMs` 기준으로 foreground vs background가 갈린다.
- `background: true`면 처음부터 session으로 간다.

즉 `exec`는 sync/async를 모두 포괄하는 shell entry point다.

### process

`process`는 background session을 다루는 도구다.

대표 action:

- `list`
- `poll`
- `log`
- `write`
- `kill`
- `clear`
- `remove`

즉 OpenClaw는 long-running shell work를 Gateway runtime 차원에서 다룬다.

### background session의 의미

background process session은 Gateway 메모리 안에 유지된다.

즉 LLM turn이 끝나도:

- process state
- output
- exit status

는 Gateway가 계속 들고 있다.

중요한 점:

- 지속성을 LLM이 가지는 게 아니다.
- 지속성을 Gateway runtime이 가진다.

## exec approvals

OpenClaw에는 `exec`용 승인 개념이 있다.

핵심 흐름:

- 위험하거나 미승인된 명령은 바로 실행하지 않는다.
- approval request 상태로 둔다.
- 사용자에게 승인 요청을 보낸다.
- `allow once`, `allow always`, `deny` 같은 결정을 받는다.

즉 shell safety는 단순 denylist에 머무르지 않고, **대화형 approval flow**로 이어질 수 있다.

이 approval은 UI 버튼일 수도 있고, 채널/자연어 응답일 수도 있다.

## background completion notification

OpenClaw에는 Gateway-native background completion notification 개념이 있다.

핵심 축:

- `tools.exec.notifyOnExit`
- system event enqueue
- heartbeat 요청

흐름:

1. background exec 실행 중
2. Gateway가 종료 감지
3. Gateway가 system event 생성
4. heartbeat를 요청
5. agent/LLM이 다시 깨어나 이벤트를 처리

중요한 점:

- 종료 감지 자체는 LLM이 아니라 Gateway가 한다.
- heartbeat는 completion event를 처리하도록 agent loop를 다시 깨우는 장치다.

즉 completion notification은 OpenClaw에서 **runtime 기능**이다.

## logs

OpenClaw에는 두 층의 log 감각이 있다.

### Gateway logs

이건 daemon 자체의 로그다.

- 로그 파일
- `openclaw logs --follow`
- Control UI의 Logs 탭

즉 운영 디버깅과 daemon 관찰용 로그다.

### process/session logs

이건 background process의 stdout/stderr 같은 session output이다.

`process poll/log`로 본다.

즉 OpenClaw는 runtime log와 session log를 구분해 다룬다.

## heartbeat

heartbeat는 OpenClaw에서 event-driven wakeup 개념에 가깝다.

중요한 점:

- 프로세스 완료를 heartbeat가 직접 감시하는 건 아니다.
- Gateway가 이벤트를 만들고, heartbeat는 그 이벤트를 처리하도록 run을 다시 깨우는 역할을 한다.

즉 heartbeat는 단순한 타이머라기보다, **Gateway event를 agent loop로 다시 연결하는 bridge**에 가깝다.

## doctor

`doctor`는 단순 로그 뷰어가 아니다.

성격:

- 진단
- 수리
- migration
- health check

예:

- config/state 점검
- stale state 정리
- service drift 점검
- auth/환경 문제 감지
- legacy migration

즉 doctor는 OpenClaw에서 **Gateway 운영 상태를 점검하고 고치는 명시적 도구**다.

## Lobster

Lobster는 OpenClaw의 workflow shell 개념이다.

역할:

- multi-step deterministic workflow 실행
- approval checkpoint
- resume token
- 하나의 pipeline run으로 여러 tool step을 감싼다

즉 Lobster는 단순 tool 하나라기보다, **Gateway 위에서 돌아가는 workflow runtime**에 가깝다.

중요한 점:

- OpenClaw의 기본 코어는 Gateway다.
- Lobster는 그 위에서 특히 강력한 workflow abstraction을 제공하는 층이다.

## OpenClaw의 성격

OpenClaw를 한 문장으로 요약하면 이렇다.

- gateway-centered
- tool-platform oriented
- event-driven
- session/runtime aware
- background/native automation friendly

즉 OpenClaw는 "대화를 잘하는 assistant"이기도 하지만, 더 본질적으로는 **Gateway가 agent system 전체를 운영하는 control-plane product**에 가깝다.
