# 개인용 AI 디스코드 봇

나만 쓰는 **유저 설치(User Install)** AI 봇. ChatGPT 구독으로 돌아가고, DM·아무 서버·그룹 DM 어디서든 부를 수 있다.
날씨, 환율, 링크/유튜브 읽기, 웹 검색, 리마인더는 따로 명령어 없이 AI가 알아서 도구로 쓴다.

## 쓰는 법
| 어디서 | 방법 |
|---|---|
| 봇 DM | 그냥 말 걸기. 이미지 첨부, 다른 채팅의 메시지 **전달(Forward)** 도 읽음 |
| 아무 서버 | `/c 질문` (이미지 첨부 가능). `/c`만 치고 엔터 → 여러 줄 입력창 |
| 답변 밑 버튼 | `💬 이어서 질문`(다른 사람도 누를 수 있음), `🧹 새 대화`(나만) |
| 남의 메시지 | 길게 누르기/우클릭 > 앱 > `AI에게 묻기` (요약·번역·링크·이미지) |

예: `/c 내일 부산 비 와?`, `/c 100달러 원화로`, `/c 이거 요약 https://...`, `/c 6시에 빨래 알려줘`, `/c 서버 상태`

설정: `/ai login`, `/ai logout`, `/ai model [name] [effort]`, `/ai reset`(채널 기록 삭제)

## 페르소나와 기억
- `/ai persona`: AI 성격/말투/이름 편집 (입력창)
- 대화가 끝나고 90초쯤 조용하면(`AI_MEMORY_IDLE`) 뒤에서 따로 AI를 한 번 더 돌려 기억을 정리한다: 새 사실 저장, 바뀐 건 수정, 중복 합치기, 틀린 건 삭제. `/ai tidy [user]`로 바로 정리할 수도 있다. 사용량이 조금 더 들고, `AI_MEMORY_AUTO=0`으로 끈다.
- 기억은 **메인 기억**(모든 대화에 들어감, 손님과의 대화 포함)과 **사람별 기억**(그 사람과 대화할 때만 들어감)으로 나뉜다. "그거 잊어", "나 이제 서울 살아"처럼 말하면 지우거나 고친다.
- `/ai memory [user]`: 기억 보기, `/ai forget id` 또는 `/ai forget user:@사람`: 지우기
- 손님은 자기 기억만 쌓이고 고칠 수 있고, 메인 기억이나 주인 기억은 못 건드린다. scope별 최대 50개, 넘으면 오래된 것부터 지워진다.

대화는 채널별로 최근 20개 메시지를 기억한다. 다른 사람이 버튼으로 물으면 질문이 답변 위에 인용되고, 횟수는 `AI_GUEST_LIMIT`(기본 10분에 5번)로 제한된다. 다른 사람은 리마인더/서버 도구를 못 쓴다.

## 디스코드 설정
1. https://discord.com/developers/applications 에서 앱 생성
2. **Installation**: Installation Contexts에서 *User Install*만 체크. Install Link는 *Discord Provided Link*, 스코프 `applications.commands`
3. **Bot**: 토큰 발급, *Public Bot* 끄기
4. Installation 페이지 링크로 내 계정에 설치

## 실행
```sh
cp .env.example .env   # 토큰, OWNER_ID 입력
docker compose up -d --build
```
Docker 없이: `python -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m bot` (systemd 유닛은 `deploy/discord-bot.service`)

봇이 뜨면 `/ai login` → 링크에서 ChatGPT 로그인 후 코드 입력.

## ChatGPT 연결
API 키 대신 ChatGPT 구독을 쓴다. Codex CLI의 "Sign in with ChatGPT"와 같은 OAuth 디바이스 코드 흐름이라 사용량은 Codex 한도에서 차감된다. 토큰은 `data/chatgpt-auth.json`에 저장되고 자동 갱신된다. 비공식 사용이라 OpenAI가 막으면 멈출 수 있다.

## 서버 감시
1분마다 메모리, 디스크, load를 확인해서 `.env`의 `ALERT_*` 임계값을 넘으면 DM을 보낸다.

## 구조
- `bot/chatgpt.py`: OAuth 로그인/갱신, Responses API 스트리밍
- `bot/aitools.py`: AI가 쓰는 도구 (날씨 Open-Meteo, 환율 open.er-api, 링크/유튜브, 리마인더, 서버 상태). 도구 추가는 여기에
- `bot/memory.py`: 페르소나, 메인/사람별 기억 (SQLite)
- `bot/curator.py`: 대화 후 백그라운드 기억 정리
- `bot/cogs/ai.py`: `/c`, 버튼, DM, 우클릭 메뉴, `/ai` 설정
- `bot/cogs/reminders.py`, `bot/cogs/monitor.py`: 리마인더 발송, 서버 감시
