# 개념

이 페이지는 KiraClaw를 이루는 핵심 개념을 설명한다. 가장 먼저 답하려는 질문은 이거다.

**KiraClaw는 어떤 종류의 시스템인가?**

KiraClaw는 단순히 채팅 UI 뒤에 모델이 있는 구조가 아니다. 항상 켜져 있는 로컬 daemon, 여러 client surface, 명시적인 tool 사용, 로컬 상태 관찰성을 중심으로 만든 desktop agent runtime이다.

## 핵심 아이디어

KiraClaw는 세 가지 생각 위에 서 있다.

- `agentd`가 오래 살아 있는 로컬 daemon이다.
- `desktop`과 채널 adapter가 `agentd`에 붙는 client surface다.
- 재사용 가능한 core runtime이 핵심 agent loop를 제공하고, KiraClaw가 그 위에 제품용 tool과 동작을 얹는다.

즉 KiraClaw는 서로 분리된 여러 봇보다는, 여러 귀와 입을 가진 하나의 agent system처럼 동작한다.

## daemon 경계

KiraClaw의 중심은 `agentd`다.

`agentd`가 소유하는 것:

- sessions
- scheduler state
- memory runtime
- MCP runtime
- channel runtimes
- background processes

구현상으로는 FastAPI 기반 로컬 서버지만, 개념상으로는 KiraClaw의 로컬 daemon이자 실행 경계로 보는 것이 더 정확하다.

이게 중요한 이유는 desktop 앱, Talk surface, 채널들이 모두 같은 runtime으로 수렴하기 때문이다.

## Desktop surface

desktop 앱은 `agentd`에 붙는 기본 local client다.

현재 주요 표면:

- `Talk`
- `Logs`
- `Diagnostics`
- `Skills`
- `Schedules`
- `Settings`

이 표면들은 서로 다른 역할을 가진다.

- `Talk`는 직접 대화하는 local surface다.
- `Logs`는 run 중심 agent trace를 보여준다.
- `Diagnostics`는 daemon 중심 resources와 structured daemon events를 보여준다.

즉 desktop은 단순한 채팅 창이 아니라, 대화와 제어를 함께 제공하는 local shell이다.

## 채널은 adapter

Slack, Telegram, Discord는 같은 runtime에 붙는 얇은 adapter로 취급된다.

각 adapter는:

1. 외부 메시지를 받는다
2. 내부 `session_id`로 정규화한다
3. 같은 session/engine 경계로 run을 연다
4. 결과를 `speak` 또는 채널 delivery로 다시 내보낸다

이 구조 덕분에 KiraClaw는 separate bot stack보다, 하나의 runtime에 여러 delivery surface가 붙은 제품에 더 가깝다.

## Runs와 sessions

KiraClaw의 기본 실행 단위는 `run`이다.

대략 이런 흐름이다.

1. 입력이 들어온다
2. `SessionManager`가 `RunRecord`를 만든다
3. 해당 session lane queue에 run을 넣는다
4. conversation context와 memory context를 준비한다
5. `KiraClawEngine.run()`이 agent를 실행한다
6. 결과를 `speak`, `submit`, logs로 정리한다

즉 KiraClaw는 기본적으로 요청 하나를 run 하나로 처리한다.

세션은 그 run들을 이어주는 연속성 단위다.

예:

- `desktop:local`
- `schedule:<id>`
- Slack channel/thread session
- Telegram chat/thread session
- Discord channel/thread session

하나의 session 안에서는:

- runs가 직렬화되고
- 최근 run 기록이 남고
- idle timeout 이후 lane이 정리된다

## Logs와 Diagnostics

KiraClaw는 의도적으로 두 종류의 관찰 표면을 나눈다.

### Logs

`Logs`는 **agent run**을 위한 화면이다.

보이는 것:

- prompt
- streamed text
- tool start/end
- tool result
- spoken reply
- internal summary
- error

즉 `Logs`는 **이 요청을 agent가 어떻게 처리했는가**를 보는 표면이다.

### Diagnostics

`Diagnostics`는 **daemon state**를 위한 화면이다.

기반 API:

- `GET /v1/resources`
- `GET /v1/daemon-events`

보이는 것:

- channel 상태
- memory runtime 상태
- MCP runtime 상태
- scheduler 상태
- process 상태

즉 `Diagnostics`는 요청 하나의 처리 기록이 아니라, **daemon이 지금 무엇을 관리하고 있는가**를 보여준다.

이 둘을 나눈 이유는 KiraClaw가 agent trace와 daemon state를 같은 종류의 로그로 보지 않기 때문이다.

## Memory

KiraClaw의 durable memory는 workspace 안에 저장된다.

기본 구조:

- `workspace/memories/*.md`
- `workspace/memories/index.json`

주요 category:

- `users`
- `channels`
- `misc`

agent-facing 도구:

- `memory_search`
- `memory_save`
- `memory_index_search`
- `memory_index_save`

즉 KiraClaw memory는 모델 내부의 hidden state보다는, 인덱스가 붙은 로컬 memory store에 가깝다.

## Tool 모델

KiraClaw는 네 층 정도의 tool 구조를 가진다.

### Core base tools

- `bash`
- `read`
- `write`
- `edit`
- `grep`
- `glob`
- `submit`
- `skill`

### KiraClaw native tools

- `speak`
- `memory_*`
- Slack tools
- Telegram tools
- Discord tools
- `exec`
- `process`

### MCP tools

MCP runtime이 붙이는 외부 integration / retrieval tool들.

### skills

`skills/` 아래 `SKILL.md`로 관리되는 workspace instruction package.

즉 KiraClaw는 pure tool platform이라기보다, layered tool surface를 가진 assistant product다.

## speak 와 submit

KiraClaw의 중요한 설계 포인트는 `submit`과 `speak`를 분리한 것이다.

- `submit`
  - run을 내부적으로 끝낸다
  - final summary를 확정한다
- `speak`
  - 바깥 사용자에게 실제로 말한다

이 분리 덕분에 가능한 것:

- 내부 작업만 하고 조용히 끝내기
- 매 스케줄 실행마다 무조건 바깥에 말하지 않기
- 내부 완료와 외부 발화를 분리하기

즉 KiraClaw는 단순 "항상 응답하는 봇"보다 assistant runtime에 더 가깝게 동작한다.

## Background work

KiraClaw는 이제 background process 개념을 가진다.

핵심 구성:

- `BackgroundProcessManager`
- `exec`
- `process`

### exec

`exec`는 오래 걸릴 수 있는 shell 작업을 시작한다.

- 빨리 끝나면 바로 결과를 돌려준다
- 오래 걸리면 `session_id`를 돌려준다

### process

`process`는 이미 시작된 background session을 다룬다.

지원 action:

- `list`
- `poll`
- `log`
- `kill`
- `clear`

즉 KiraClaw는 이제 긴 shell 작업을 daemon 수명으로 붙들고 나중에 다시 볼 수 있다.

## Scheduler

KiraClaw의 scheduler는 시간 기반으로 새 run을 여는 메커니즘이다.

의미상으로는:

- 사용자가 직접 run을 여는 것
- scheduler가 시간이 되어 run을 여는 것

둘 다 결국 daemon 안에서 새 run을 여는 동일한 구조다.

즉 scheduler는 현재 KiraClaw의 time-based wakeup 모델이다.

중요한 점은 scheduled run이 항상 말할 필요는 없다는 것이다. 이상이 없으면 조용히 끝날 수 있다.

## Safety 와 approval 방향

`bash`와 `exec`는 shell safety 규칙을 탄다.

현재 규칙 종류:

- `DENY`
- `ALLOW`
- `ASK`

지금 KiraClaw는 완전한 approval system보다는 deny-first safety 구조에 가깝다. 위험한 패턴은 막고, 안전한 패턴은 허용하며, 자연어 approval flow는 아직 앞으로 들어올 개념이다.

현재 더 잘 맞는 방향은 자연어 기반 approval이다.

- pending approval을 session state에 저장하고
- 자연어로 승인 요청을 하고
- 다음 답변을 `승인 / 거절 / 항상 허용`으로 해석하고
- 승인 후 원래 작업을 이어가는 흐름

## 현재 KiraClaw의 성격

지금 KiraClaw는 이렇게 요약할 수 있다.

- conversation-centric
- local-daemon-based
- channel-aware
- run-oriented

그리고 최근 여기에 추가된 축은 이렇다.

- background processes
- structured diagnostics
- 조금 더 명시적인 local control-plane 토대

즉 KiraClaw는 daemon 구조를 이미 상당히 갖추고 있지만, 핵심 감각은 여전히 assistant product 쪽에 있다.

## OpenClaw에서 영향을 받은 아이디어

KiraClaw는 OpenClaw의 몇 가지 개념에서도 영향을 받는다.

대표적으로:

- gateway-centered runtime thinking
- `exec / process`를 first-class shell primitive로 두는 생각
- 자연어 approvals
- background completion notification
- event-driven wakeup 개념
- Lobster 같은 workflow-shell 아이디어

KiraClaw가 이걸 전부 구현하고 있지는 않지만, 앞으로 확장될 방향을 생각할 때 중요한 참고점이 된다.
