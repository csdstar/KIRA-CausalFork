# KiraClaw Concepts

이 문서는 KiraClaw를 이루는 핵심 개념을 제품 내부 관점에서 정리한 문서다. 비교나 외부 레퍼런스보다는, 현재 KiraClaw가 무엇으로 구성되어 있고 어떤 철학으로 동작하는지에 집중한다.

## 한 줄 정의

KiraClaw는 **항상 켜져 있는 로컬 daemon 위에서, desktop과 여러 채널 표면을 통해 에이전트를 실행하는 assistant product**다.

핵심은 이 세 가지다.

- `agentd`가 오래 살아 있는 로컬 runtime이다.
- `desktop`과 채널 adapter가 `agentd`에 붙는 client surface다.
- 실제 에이전트 reasoning은 core runtime 위에서 돌아간다.

## 핵심 구성 요소

### agentd

`agentd`는 KiraClaw의 중심이다.

- FastAPI 기반 long-running 로컬 서버다.
- session, scheduler, memory, MCP, channel runtime을 소유한다.
- desktop과 외부 채널이 모두 여기에 붙는다.
- 현재 KiraClaw에서 gateway에 가장 가까운 개념이다.

단순한 API 서버라기보다, **제품의 실행 경계이자 로컬 daemon**으로 보는 게 맞다.

### desktop

desktop은 `agentd`에 붙는 기본 client다.

주요 표면:

- `Talk`
- `Logs`
- `Diagnostics`
- `Skills`
- `Schedules`
- `Settings`

desktop은 단순 UI가 아니라, 로컬 daemon을 제어하고 상태를 관찰하는 기본 control surface이기도 하다.

### channel adapters

현재 KiraClaw는 다음 채널을 adapter로 연결한다.

- Slack
- Telegram
- Discord

각 adapter는 외부 메시지를 받아 내부 `session_id`로 정규화한 뒤, `SessionManager`를 통해 run을 연다. 결과는 `speak` 또는 채널 delivery를 통해 다시 외부로 나간다.

### Core runtime

KiraClaw의 agent loop는 core runtime 위에서 돈다.

- core base tools를 사용한다.
- KiraClaw가 native tools를 추가한다.
- MCP tools와 skills를 함께 붙인다.

즉 KiraClaw는 직접 agent runtime을 처음부터 구현한 제품이라기보다, **재사용 가능한 core runtime을 host하면서 product-specific tool layer를 얹은 구조**다.

## 실행 흐름

KiraClaw의 기본 실행 단위는 `run`이다.

대략적인 흐름:

1. 입력이 들어온다.
2. `SessionManager`가 `RunRecord`를 만든다.
3. 해당 `session_id`의 lane queue에 run을 넣는다.
4. conversation context와 memory context를 만든다.
5. `KiraClawEngine.run()`이 core agent를 실행한다.
6. 결과를 `speak`, `submit`, run logs로 정리한다.

즉 KiraClaw는 기본적으로 **요청 하나를 run 하나로 처리하는 구조**다.

## 세션

세션은 KiraClaw에서 대화를 이어붙이는 기본 단위다.

현재 세션은 주로 adapter와 runtime이 만든다.

예:

- `desktop:local`
- `schedule:<id>`
- Slack channel/thread 기반 session
- Telegram chat/thread 기반 session
- Discord channel/thread 기반 session

현재 세션 구조의 핵심:

- 같은 세션 안에서는 run이 직렬화된다.
- 최근 run record가 메모리에 유지된다.
- idle timeout이 지나면 lane이 정리된다.

즉 KiraClaw의 세션은 현재 **adapter-driven session routing + session lane queue**로 이해하면 된다.

## 로그와 진단

KiraClaw에는 현재 두 종류의 관찰 표면이 있다.

### run logs

`Logs` 메뉴가 보여주는 것은 run logs다.

여기에는 요청 하나에 대한 agent trace가 들어간다.

- prompt
- streamed text
- tool start/end
- tool result
- spoken reply
- internal summary
- error

즉 run logs는 **에이전트가 이 요청을 어떻게 처리했는지**를 보는 기록이다.

### daemon diagnostics

`Diagnostics` 메뉴가 보여주는 것은 daemon 차원의 상태다.

현재 API:

- `/v1/resources`
- `/v1/daemon-events`

여기서 보는 것은 예를 들어 이런 것들이다.

- channel 상태
- memory runtime 상태
- mcp runtime 상태
- scheduler 상태
- process 상태

즉 Diagnostics는 **요청 하나의 처리 기록이 아니라, daemon이 지금 무엇을 관리하고 있는지**를 보는 표면이다.

### daemon logs와의 차이

중요한 점:

- `run logs`는 agent run 기록이다.
- `daemon events`는 구조화된 daemon 상태 변화다.
- raw daemon stdout/stderr logs는 아직 별도 표면이 없다.

즉 현재 Diagnostics는 진짜 서버 콘솔 로그를 보여주는 화면이 아니라, **daemon resources / events를 보여주는 화면**이다.

## 메모리

KiraClaw의 durable memory는 workspace 안에 저장된다.

기본 구조:

- `workspace/memories/*.md`
- `workspace/memories/index.json`

대표 category:

- `users`
- `channels`
- `misc`

agent-facing 도구:

- `memory_search`
- `memory_save`
- `memory_index_search`
- `memory_index_save`

또한 `MemoryRuntime`이 run 전에 memory context를 붙이고, 저장은 async queue로 처리한다.

즉 KiraClaw memory는 daily log보다는 **indexed memory store**에 가깝다.

## 툴 구조

KiraClaw의 tool surface는 대략 네 층으로 나뉜다.

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

MCP runtime이 연결한 외부 integration / retrieval tool이 별도 tool surface로 붙는다.

### skills

workspace의 `skills/` 아래 `SKILL.md` 패키지를 로드해 workflow instruction으로 사용한다.

정리하면 KiraClaw는 **core base tools + native tools + MCP + skills** 구조다.

## speak 와 submit

KiraClaw에서 중요한 개념 분리는 `submit`과 `speak`다.

- `submit`
  - 내부적으로 run을 끝낸다.
  - final summary를 확정한다.
- `speak`
  - 외부 사용자에게 실제로 말한다.

이 분리 덕분에 가능한 패턴:

- run은 끝났지만 외부 채널에는 아무 말도 안 하기
- 내부 작업만 하고 조용히 끝내기
- scheduler가 run을 깨운 뒤, 이상이 있을 때만 말하기

즉 KiraClaw는 **내부 완료와 외부 발화를 분리한 구조**를 갖는다.

## background exec / process

최근 KiraClaw에는 background process 개념이 추가되었다.

핵심 구성:

- `BackgroundProcessManager`
- `exec`
- `process`

### exec

`exec`는 오래 걸릴 수 있는 shell 작업을 시작하는 도구다.

- 짧게 끝나면 바로 결과를 돌려준다.
- 오래 걸리면 `session_id`를 돌려준다.

### process

`process`는 이미 시작된 background session을 다룬다.

지원 action:

- `list`
- `poll`
- `log`
- `kill`
- `clear`

### 현재 의미

이 구조 덕분에 KiraClaw는 이제:

- 긴 build
- 테스트
- 서버 실행
- 장기 shell 작업

을 daemon 수명으로 붙들고 나중에 다시 조회할 수 있다.

즉 background process는 기존의 "요청 하나가 끝나면 같이 끝나는 run"과 달리, **run 밖으로 살아남는 작업 단위**다.

### 아직 없는 것

현재는 아직 다음 기능이 없다.

- 자동 완료 알림
- notify-on-exit
- heartbeat 기반 wakeup
- daemon 재시작 후 세션 유지

즉 현재 `exec/process`는 **수동 조회형 background process v1**이다.

## 스케줄러

KiraClaw의 scheduler는 시간 기반으로 새 run을 여는 메커니즘이다.

현재 구현은 APScheduler 기반이다.

의미상으로는:

- 사용자가 직접 요청해서 run 생성
- scheduler가 시간이 되어 run 생성

둘 다 결국 daemon 안에서 새 run을 여는 동일한 구조다.

즉 scheduler는 현재 KiraClaw에서 **time-based wakeup** 역할을 한다.

중요한 점:

- 스케줄이 run을 열어도 `speak`를 반드시 할 필요는 없다.
- 이상이 없으면 조용히 `submit`만 하고 끝낼 수 있다.

이 패턴 덕분에 KiraClaw는 heartbeat가 없어도 많은 자동 점검 시나리오를 처리할 수 있다.

## 명령 안전 규칙

`bash`와 `exec`는 둘 다 shell safety 규칙을 탄다.

현재 개념:

- `DENY`
  - 위험 패턴이면 바로 차단
- `ALLOW`
  - allowlist prefix면 바로 실행
- `ASK`
  - 승인 필요

기본 deny 예:

- `rm -rf /`
- `dd if=`
- `curl|sh`

기본 allow 예:

- `ls`
- `cat`
- `rg`
- `git status`
- `pytest`

중요한 현재 상태:

- KiraClaw 기본값은 `ask_by_default = false`
- 그래서 대부분은 `ALLOW` 또는 사실상 허용
- 진짜 위험 패턴만 `DENY`

즉 지금은 **approval system이 중심인 구조가 아니라, deny-first safety 구조**에 가깝다.

## 승인(approval) 개념

현재 KiraClaw에는 완전한 자연어 approval flow가 아직 없다.

다만 개념적으로 필요한 방향은 분명하다.

- `ASK`가 나오면 바로 실패시키지 않기
- pending approval을 세션 상태로 저장하기
- 자연어로 승인 요청하기
- 사용자의 다음 답변에서 `승인 / 거절 / 항상 허용`을 해석하기
- 승인 후 원래 명령을 계속 진행하기

즉 KiraClaw에 앞으로 들어올 수 있는 approval은 버튼 UI보다, **대화 기반 자연어 승인**이 더 잘 맞는다.

## 현재 KiraClaw의 성격

현재 KiraClaw는 다음 성격이 강하다.

- conversation-centric assistant
- local daemon 기반 product
- channel-aware agent
- run-oriented execution model

그리고 최근 추가된 축은 이렇다.

- background process
- daemon diagnostics
- 조금 더 명시적인 control-plane 토대

즉 KiraClaw는 지금도 충분히 daemon-centered 구조를 갖고 있지만, 여전히 핵심 감각은 **"대화를 잘 처리하는 assistant product"**에 더 가깝다.

## 지금 없는 것

현재 KiraClaw에 아직 없는 대표 개념은 이쪽이다.

- 자연어 기반 approval flow
- resume 개념
- background completion notification
- heartbeat 기반 event wakeup
- doctor
- raw daemon log surface
- workflow runtime
- nodes

이것들은 앞으로 들어올 수 있는 확장 개념이지, 현재 코어에 이미 있는 개념은 아니다.
