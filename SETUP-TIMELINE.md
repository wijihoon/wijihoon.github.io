# 초기 세팅 타임라인 — 수익 다각화 로드맵

> 원칙: 각 단계는 **한 번만** 하면 되고, 이후엔 사람 개입 0.
> 시크릿을 추가하는 순간 그 채널/수익원이 **자동으로 켜집니다** (코드 수정 불필요).

## 📅 Day 0 — 오늘 (약 40분) : 기본 가동

| 순서 | 작업                                           | 시간  | 결과               |
|----|----------------------------------------------|-----|------------------|
| 1  | 이 repo를 GitHub에 push (public)                | 5분  | 코드 준비            |
| 2  | Settings→Pages: `main` 브랜치 배포                | 2분  | **채널① 내 블로그** 생김 |
| 3  | Settings→Secrets: `GROQ_API_KEY` 추가          | 3분  | 글 작성 엔진          |
| 4  | 디스코드 채널→연동→웹후크 생성 → `DISCORD_WEBHOOK_URL` 추가 | 5분  | 시작/종료/에러 알림      |
| 5  | Actions→Run workflow로 첫 실행 테스트               | 5분  | 첫 글 게시 확인        |
| 6  | **쿠팡 파트너스 가입 신청** (partners.coupang.com)     | 10분 | 승인 대기 시작 (1~3일)  |
| 7  | **구글 애드센스 가입 신청** (adsense.google.com)       | 10분 | 심사 대기 시작 (1~4주)  |

## 📅 Day 1~2 (약 30분) : 채널② Blogger (애드센스와 궁합 최고)

1. blogger.com에서 블로그 생성 → 주소의 blogID 메모 (5분)
2. console.cloud.google.com → 프로젝트 생성 → **Blogger API v3 활성화** (5분)
3. OAuth 클라이언트(데스크톱) 생성 → OAuth Playground(developers.google.com/oauthplayground)에서
   Blogger 스코프 승인 → **Refresh Token** 발급 (15분)
4. Secrets 4개 추가: `GOOGLE_CLIENT_ID` `GOOGLE_CLIENT_SECRET` `GOOGLE_REFRESH_TOKEN` `BLOGGER_BLOG_ID`
   → 다음 실행부터 Blogger에도 자동 게시 ✅

## 📅 Day 2~4 (승인 오면 10분) : 수익원① 쿠팡 파트너스

1. 승인 후 파트너스 → 추가기능 → **OpenAPI 키 발급**
2. Secrets 추가: `COUPANG_ACCESS_KEY` `COUPANG_SECRET_KEY`
   → 모든 글 하단에 **관련 상품 3개 + 법정 고지문**이 자동 삽입, 클릭 구매 시 수수료 💰

## 📅 Day 3~7 (약 30분) : 채널③ 네이버 블로그 (국내 트래픽 최대)

1. developers.naver.com → 앱 등록 → **블로그 API** 사용 설정 (10분)
2. 본인 계정으로 OAuth 동의 → **Refresh Token** 발급 (15분)
3. Secrets 3개: `NAVER_CLIENT_ID` `NAVER_CLIENT_SECRET` `NAVER_REFRESH_TOKEN`
   → 네이버 블로그 자동 게시 ✅ (⚠️ 네이버는 하루 1~2건 권장 — 과다 게시는 저품질 위험)

## 📅 Day 7~14 : 수익원② 카카오 애드핏 (애드센스 대기 중 대안)

1. adfit.kakao.com 가입 → 사이트(GitHub Pages 주소) 등록 → 광고단위 생성
2. Secret 추가: `ADFIT_UNIT` → 본문 중간 배너 자동 삽입 💰

## 📅 Day 14~30 : 수익원③ 애드센스 (메인 수익)

- 승인 나면: `_config.yml`의 `adsense_client: "ca-pub-…"` 입력(자동광고)
    - Secret `ADSENSE_CLIENT`도 추가 → 본문 중간 인아티클 광고 삽입
- Blogger는 설정→수익에서 같은 애드센스 연결(코드 불필요)
- ⚠️ 심사 팁: 글 20개+ 쌓인 뒤 신청하면 승인율↑. 자동 글이라도 구조·정보성이 좋아야 함(이번 v2가 2패스 퇴고하는 이유)

## 📅 Day 14+ (선택) : 채널④ dev.to — IT 글 영어 확장

- dev.to 가입 → Settings→Extensions→API Key → Secret `DEVTO_API_KEY`

## 📅 30일+ : 운영

- 디스코드 알림으로 일일 결과 모니터링(개입 불필요)
- 수익 확인: 쿠팡 파트너스 대시보드 / 애드센스 / 애드핏 각 사이트
- 확장 아이디어: MAX_POSTS 조정, 카테고리 추가, 네이버 애드포스트(블로그 방문자 쌓이면) 신청

## ⚠️ 정직한 주의사항

- **티스토리/미디엄은 API가 폐지**되어 완전 자동은 불가(제외함)
- 애드센스는 자동 생성 콘텐츠에 엄격 → 승인 전 글 품질·개수 확보가 관건
- 네이버는 도배성 자동 게시에 민감 → MAX_POSTS 기본 3, 네이버는 그중 카테고리 대표 글만
- 수익은 트래픽에 비례 — 초기 1~2달은 소액이 정상. 채널·글이 쌓이며 복리로 성장
