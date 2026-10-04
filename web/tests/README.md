# 브라우저 검사

`fixtures.ts`는 합성 스냅샷과 가짜 API만 제공한다. 실제 Notion 계정·토큰·운동 기록을 읽거나 수정하지 않는다. 제품에는 이 데이터를 포함하지 않는다.

```bash
npm ci
npx playwright install --with-deps chromium webkit
npm run test:browser
```

PC 1440×900·1920×1080, 태블릿 768px, 모바일 390×844·430×932·320px에서 전체 페이지 가로 넘침과 첫 화면 밀도를 확인한다. 각 화면은 Playwright 보고서에 PNG로 첨부한다. WebKit 검사는 실제 iPhone에서의 LAN 접속 검사와 별도다.

접속 시 조회, 이전 데이터 유지, 첫 조회 실패, 수동 재조회, 서울 자정 후 갱신 안내, 기간·유형 필터, 시스템 테마, 종목 설정 저장·순서 변경, C01–C10 접근, 기준 원본 변경, 선택 건강 원본 실패, 모바일 세트 펼침, 키보드, CSV·PNG 다운로드를 검사한다. CSV는 BOM·한글·원값을, PNG는 파일 시그니처·해상도·어두운 배경을 확인한다. 분석 산식과 Notion 읽기 전용 검증은 Python 검사에서 별도로 수행한다.

최근 수행은 기본 3개와 더 보기·접기, 분석 중 스냅샷 충돌 복구를 검사한다. 현재 구성은 엔진별 26개, 총 52개 검사다.

루트 권한이 없는 준비된 클라우드에서는 `.local/browser-libraries`의 검증된 Debian 공유 라이브러리를 사용한다. 이 환경의 개인 설치 도우미 `.local/verify-browser-libraries.py`는 공식 키링의 서명, 서명된 패키지 목록과 각 `.deb`의 SHA-256을 대조하고 압축을 푼다. 다음 실행은 Playwright의 공식 WebKit 실행 파일을 명시하며 TLS·서명·체크섬 검증을 해제하지 않는다.

```bash
FITNESS_WEBKIT_EXECUTABLE_PATH="$(node -e 'process.stdout.write(require("playwright").webkit.executablePath())')" \
LD_LIBRARY_PATH=/workspace/Notion-Fitness-Tracker/.local/browser-libraries/root/usr/lib/x86_64-linux-gnu \
npm run test:browser
```

개인 PC와 CI는 위의 일반 `--with-deps` 설치를 사용한다. `.local/` 도우미와 라이브러리는 준비된 클라우드 파일이며 저장소에 포함하지 않는다.

브라우저 날짜는 합성 조회 시점인 2026-10-04로 고정한다. 각 레이아웃 검사는 PNG와 요소의 위치·크기 JSON을 `test-results`에 저장한다. 실행 시점이 바뀌어도 예시 스냅샷을 새 조회 결과처럼 오인하지 않게 하고, 분석 검사와 화면 검사를 반복할 수 있게 한다.
