# naver-gmail-bridge

네이버 회사 메일(bkeng6331@naver.com)로 온 새 메일을 지메일(bkeng6331@gmail.com)로 넘긴다.

- 왜: 네이버 개인/단체 메일은 자동 전달이 없고, 지메일은 외부 메일 가져오기(POP)를 2026년부터 없앤다.
- 어떻게: 깃허브 자동 실행이 5분마다 네이버를 IMAP으로 보고, 새 메일을 네이버 SMTP로 지메일에 보낸다.
  보낸 사람 이름에 원래 보낸 사람, Reply-To에 원래 주소를 둬서 지메일에서 답장하면 원래 사람에게 간다.
- 지메일 → 네이버 주소로 보내기: 지메일 설정의 '다른 주소에서 메일 보내기'(smtp.naver.com:465 SSL, 기본 주소).
- 37MB 넘는 메일: 먼저 첨부를 34MB 묶음으로 나눠 여러 통("(1/2) 제목", 첫 통에 본문)으로 첨부째 보낸다(파일 하나가 34MB 이하일 때, 대략 50MB 메일까지).
- 그래도 안 되면(파일 하나가 너무 큼): 첨부는 비공개 저장소 Castle0628/naver-mail-files 의 Releases에 올리고, 지메일엔 본문+내려받기 주소(비밀 칸 FILES_TOKEN 필요).
- 용량 청소(사용자 승인 2026-11-20): 다리가 보낸 사본은 바로, 지메일에서 보낸 메일의 네이버 사본은 3일 뒤(2026-11-20 이후 것만), 광고·알림 폴더는 30일 뒤, 휴지통은 7일 뒤 삭제. 용량 95% 넘으면 지메일로 경고(하루 한 번).
- 보낸 사람 이름은 '이름 · 도메인'(이름 칸에 @주소를 넣으면 지메일이 사칭 경고를 띄움). 시험: `python e2e.py`(가상 거래처 메일을 네이버에 넣음).
- 넘긴 원본은 3일 뒤 네이버에서 영구 삭제(휴지통 포함). 사용자 승인 2026-11-20.
- 넘기지 않는 폴더: 보낸·임시·휴지통·스팸·템플릿, 프로모션·청구·결제·SNS·카페·알바몬.
- 넘길 때 읽음 표시는 안 바꿈. `state.json` = 폴더별 다음에 볼 UID + 삭제 대기 목록(`_pending`).
- 비밀번호: 깃허브 비밀 칸 `NAVER_APP_PASSWORD`(네이버 애플리케이션 비밀번호). 코드에는 없음.

도는 방식: 깃허브 예약(cron)이 이 저장소에서 안 돌아서(2026-11-20, 2시간 0회), 한 번 실행이 약 5시간 40분 동안 5분마다 돌고 끝날 때 자기 자신을 다시 실행한다. 체인이 끊기면 Actions 탭에서 Run workflow 한 번이면 다시 이어진다.
지킴이: 비공개 저장소 Castle0628/bukyung-portal 의 `.github/workflows/mail-bridge-watchdog.yml`이 3시간마다 이 저장소에 도는 실행이 있는지 보고, 없으면 다시 켠다(비밀 칸 `MAIL_BRIDGE_TOKEN` 필요, 비면 실패 메일이 옴).

## 장애 대응

1. 지메일로 메일이 안 들어옴 → 다리가 멈춘 것. 지킴이가 3시간 안에 다시 켜지만, 바로 켜려면:
   `gh workflow run bridge.yml --repo Castle0628/naver-gmail-bridge`
   (또는 https://github.com/Castle0628/naver-gmail-bridge/actions → naver-to-gmail → Run workflow)
2. 받기·보내기 둘 다 안 됨(로그에 LOGIN 실패, 지메일 발송 오류) → 네이버 애플리케이션 비밀번호가 바뀜/지워짐.
   네이버에서 새로 만든 뒤 두 곳에 넣기:
   - `gh secret set NAVER_APP_PASSWORD --repo Castle0628/naver-gmail-bridge` (붙여 넣으라고 나옴)
   - 지메일(회사 계정) 설정 → 계정 → 다른 주소에서 메일 보내기 → bkeng6331@naver.com 정보 수정 → 새 비밀번호
3. 지킴이가 실패 메일을 보냄(MAIL_BRIDGE_TOKEN 비어 있음/만료) →
   `gh auth token | gh secret set MAIL_BRIDGE_TOKEN --repo Castle0628/bukyung-portal`
4. 실행 기록 보기: `gh run list --repo Castle0628/naver-gmail-bridge -L 5`, 로그: `gh run view <번호> --repo Castle0628/naver-gmail-bridge --log`
5. 그 밖: 새 Claude 세션에서 "naver-gmail-bridge 저장소 README 보고 고쳐 줘".

멈추기: 저장소 Actions 탭에서 워크플로 Disable.
