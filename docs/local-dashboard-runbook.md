# 로컬 웹 실행

PC에서 필요할 때 켜는 개인용 대시보드다. Notion은 기록 원본이며 웹은 조회만 한다. Burnfit → ChatGPT/MCP → Notion 기록 방식은 그대로 사용한다.

## 처음 설정

Python 3.12 이상과 Node.js 24 LTS를 설치한 뒤 저장소 디렉터리에서 실행한다. Node는 웹 설치·빌드에 사용하고, 평소에는 Python 서버 하나만 켠다.

macOS·Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
cd web
npm ci
npm run build
cd ..
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
cd web
npm ci
npm run build
cd ..
```

서버 환경 변수 `NOTION_TOKEN`에 사용 중인 Notion 통합 토큰을 제공한다. 해당 통합이 원본 DB를 읽을 수 있어야 한다. 토큰은 브라우저·연결 JSON에 넣지 않는다. 클라우드 secret은 개인 PC에 자동으로 복사되지 않는다.

토큰을 터미널 명령 기록에 남기지 않고 현재 터미널에 입력하는 예:

```bash
read -r -s -p 'Notion token: ' NOTION_TOKEN
export NOTION_TOKEN
```

```powershell
$fitnessToken = Read-Host 'Notion token' -AsSecureString
$env:NOTION_TOKEN = [System.Net.NetworkCredential]::new('', $fitnessToken).Password
```

이 방식의 환경 변수는 현재 터미널에서만 유지된다. 이후 실행에도 사용하려면 운영체제의 개인 환경 변수 설정을 이용한다.

새 PC에서는 한 번 원본을 찾는다. 이미 `.local/dashboard-config.json`이 있으면 이 단계는 건너뛴다.

```bash
.venv/bin/python -m dashboard --discover
```

Windows에서는 `.venv\Scripts\python.exe -m dashboard --discover`를 사용한다. 원본의 정확한 이름과 실제 속성을 확인해 설정을 저장한다. 같은 이름의 원본이 여러 개면 임의 선택하지 않고 연결 설정이 필요하다고 알린다.

기존 비공개 Notion 설정 파일이 있는 경우에는 대신 가져올 수 있다.

```bash
.venv/bin/python -m dashboard --import-config .local/notion-config.json
```

수동 연결은 [설정 예시](../dashboard/config.example.json)를 `.local/dashboard-config.json`으로 복사해 실제 데이터 소스·속성 ID를 매핑한다. 가져오기·원본 찾기는 기존 파일을 덮어쓰지 않는다. 웹 설정을 다시 만들 때는 기존 웹 설정을 개인 백업한 뒤 해당 파일만 옮기고 다시 실행한다. 기존 Notion 관리 설정과 원본은 삭제하지 않는다.

## 평소 실행

```bash
.venv/bin/python -m dashboard
```

Windows:

```powershell
.venv\Scripts\python.exe -m dashboard
```

실행한 PC에서 `http://127.0.0.1:8000`을 연다. 종료는 터미널에서 Ctrl+C다. 별도 로그인·유료 서버·도메인이 필요 없다. 포트가 사용 중이면 `--port 8001`처럼 바꾼다.

휴대폰은 같은 LAN에서 사용한다.

```bash
.venv/bin/python -m dashboard --lan
```

터미널에 표시된 LAN 주소를 iPhone Safari에서 연다. PC와 휴대폰이 같은 네트워크에 있어야 하며 PC가 켜져 있고 서버가 실행 중이어야 한다. 운영체제 방화벽에서 개인 네트워크의 해당 포트를 허용해야 할 수 있다. 주소가 자동으로 표시되지 않으면 PC의 LAN IP를 확인해 `--lan --allow-host 192.168.0.10`처럼 지정한다. 이 주소는 예시이며 실제 PC 주소를 쓴다.

PWA는 지원되는 보안 환경에서 정적 화면 파일만 캐시한다. LAN HTTP에서 iPhone의 설치·서비스 워커 기능은 보장하지 않는다. PC가 꺼진 상태에서 새 운동 데이터를 조회하는 기능은 제공하지 않는다.

## 읽기와 갱신

- 접속하면 마지막 성공 캐시를 보여주고 즉시 Notion을 다시 조회한다. 수동 새로고침도 원본을 다시 읽는다.
- 기간·분할·세트 유형·상세 조건 변경은 성공 스냅샷을 재계산한다. 전체 조회를 반복하지 않는다.
- 조회 중에는 이전 데이터임을 표시한다. 실패하면 마지막 성공값·시각을 유지하며 재시도할 수 있다.
- 3개 필수 원본을 모두 읽고 변경 지문을 대조한다. 선택 건강 원본은 각각 성공 시각과 오류를 표시한다.
- 원본이 수백 세트인 현재 규모에서도 전체 조회·재검증·건강 원본을 합치면 수십 초가 걸릴 수 있다. 전체 조회 제한은 120초이며 Notion 요청을 무제한 반복하지 않는다.
- `prepare`, `apply`, `live-check`, `watch`는 웹 실행에서 호출하지 않는다. 웹 갱신을 위해 Notion 차트를 재배포할 필요가 없다.

## 계산과 개인 설정

기본 핵심 종목은 벤치 프레스·스쿼트·데드 리프트·풀업이다. 실제 원본의 고유 ID를 사용하며 바벨 벤치·백 스쿼트·컨벤셔널 데드리프트의 정확한 영문 이름도 대응한다. 변형을 섞거나 중복 이름을 임의로 선택하지 않는다. 종목 설정에서 추가·제거·순서를 바꾼다.

테마·핵심 종목·기본 기간·발전 기준은 브라우저별로 저장된다. PC와 휴대폰 설정은 자동 동기화되지 않는다. 전체 원본과 토큰은 브라우저 저장소에 넣지 않는다.

조건 없는 수행도 실제 중량·반복과 계산 관측 추세로 보여준다. 검증된 PR·발전율은 같은 확인 조건에서만 계산한다. 풀업은 맨몸·추가·보조·미확인을 분리하며 0kg 입력을 자동으로 맨몸이라 확정하지 않는다.

kg·회 볼륨은 외부중량 의미까지 확인된 세트만 포함한다. 단순히 `Load (kg)`라는 이름만으로 외부중량 의미를 확정하지 않는다. 비교 기준은 선택 당시 원본 지문에 고정하며 원본 수정·삭제 시 재선택한다.

세션 관계가 없는 세트는 날짜·주차를 정할 수 없어 기간 집계에서 제외한다. 원본 점검에 제외 수를 표시하며 원본을 수정하지 않는다. 종목만 미연결인 세트와 미분류 유형은 기간 총합에 유지한다.

세트·세션 CSV는 현재 기간·필터와 원값·조회 시각을 포함한다. 차트의 PNG에는 제목·기간·단위·범례·조회 시각을 넣는다.

## 개발·검사

```bash
.venv/bin/python -m unittest discover -s tests -v
cd web
npm run build
npm test
npx playwright install --with-deps chromium webkit
npm run test:browser
```

개발 중에는 Python 서버를 켠 상태에서 `web` 디렉터리의 `npm run dev`를 사용한다. 일반 사용에는 빌드한 화면을 Python 서버가 직접 제공한다. 브라우저 검사는 합성 데이터로 반복하며 실제 계정에는 읽기 전용 대조만 한다.

설정과 마지막 성공 스냅샷은 `.local/`에 저장하고 Git에서 제외한다. 앱의 연결과 캐시를 지우려면 서버를 종료한 뒤 웹용 설정·캐시 파일만 개인 백업하고 옮긴다. 기존 `notion-*` 파일과 원본 기록은 유지한다.
