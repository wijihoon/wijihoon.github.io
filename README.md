# 자율 트렌드 블로그 봇 (무료 · 무개입)

매일 **07:00 KST**에 에이전트 팀이 자동 실행:

| 에이전트 | 역할 | 사용 서비스(무료) |
|---|---|---|
| Collector | 구글 실시간 인기 토픽 수집 | Google Trends RSS (무키) |
| Categorizer | 카테고리 분류 | Groq 무료 LLM |
| Writer | 카테고리별 블로그 글 작성 | Groq 무료 LLM |
| QA | 품질 검사(짧음/거절문구) + 재시도 | 로컬 규칙 |
| Publisher | `_posts/` md 생성 → push = 게시 | GitHub Pages |
| Notifier | 시작/수집/게시/종료/에러 알림 | Discord 웹훅 |

## 최초 1회 설정 (5분, 이후 개입 0)
1. 이 폴더를 GitHub 새 repo로 push (public이면 Pages 무료)
2. **Settings → Pages** → Source: Deploy from a branch → `main` / root → Save
3. **Settings → Secrets and variables → Actions** 에 추가:
   - `GROQ_API_KEY` — console.groq.com 무료 발급
   - `DISCORD_WEBHOOK_URL` — 디스코드 채널 설정 → 연동 → 웹후크 → URL 복사
4. 끝. 내일 07:00부터 자동. 바로 테스트: **Actions → Daily Trend Blog → Run workflow**

## 조정
- 글 개수: 워크플로 env `MAX_POSTS` (기본 5)
- 지역: `bot/main.py` `collect_trends(geo="KR")`
- 카테고리: `CATEGORIES` 리스트
# insight-daily
