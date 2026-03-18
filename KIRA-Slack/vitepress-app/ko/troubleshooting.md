# 문제 해결

## macOS에서 앱을 열 수 없다고 나와요

현재 빌드가 아직 공증되지 않았으면 첫 실행에서 macOS 경고가 뜰 수 있습니다.

이렇게 열면 됩니다.

1. Finder에서 `KiraClaw.app` 찾기
2. 앱을 우클릭
3. `열기` 선택
4. 한 번 더 확인

한 번 열고 나면 이후 실행은 보통 정상입니다.

## 아직 예전 KIRA-Slack 앱이 열려요

이 경우 아직 legacy 앱을 실행하고 있는 겁니다.

다음 앱을 열었는지 확인하세요.

- `KiraClaw.app`

예전 `KIRA` 앱이 아니라 새 `KiraClaw` 앱을 실행해야 합니다.

## KIRA-Slack이 제자리 자동 업데이트될 줄 알았어요

`KIRA-Slack`은 이제 legacy로 취급됩니다.

수동 전환 기준으로 보면 됩니다.

1. 최신 `KiraClaw` 다운로드
2. 새 앱으로 설치
3. 필요하면 기존 `~/.kira` 설정 재사용

## Slack이나 Telegram이 응답하지 않아요

데스크톱 앱에서 다음을 확인하세요.

- `Channels`
- `Runs`

runtime은 정상인데 바깥으로 말하지 않았다면, `Runs` 화면에서 다음을 보면 됩니다.

- internal summary
- spoken reply
- tool usage
- silent reason

## 로컬 파일은 어디에 있나요?

KiraClaw는 filesystem base directory 아래에 상태를 둡니다.

- `skills/`
- `memories/`
- `schedule_data/`
- `logs/`

관련 폴더는 데스크톱 앱에서 바로 열 수 있습니다.
