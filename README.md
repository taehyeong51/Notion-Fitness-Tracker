# Fitness Tracker

[로컬 웹 명세](docs/local-web-dashboard-spec.md) · [채택한 목업](docs/assets/local-dashboard-mockup.png) · [기존 Notion 구성](fitness-tracker-audit-and-plan-2026-10-03.md) · [Notion 홈](https://www.notion.so/a6cac97b85638267a21381f41263a8eb)

다음 구현은 Notion 원본을 읽는 **독립 로컬 웹 대시보드**입니다. PC에서 필요할 때 무료로 실행하며 접속·새로고침 시 조회합니다. 별도 로그인 없이 핵심 종목 선택, PC·모바일 분석, 시스템 테마를 제공합니다. 현재는 명세와 목업을 저장한 단계이며 웹 앱은 아직 구현하지 않았습니다.

기존 교육 Plus용 Notion 네이티브 구성은 유지합니다. 홈에는 기간·집계 시각이 있는 주간 요약과 최근 운동·리뷰의 연결 목록, 상세 분석에는 운동 성과·훈련 구성을 둡니다. Notion 페이지 안에 HTML 대시보드를 삽입하지 않습니다.

## 기존 Notion 도구 실행

Python 3.12 이상, 표준 라이브러리만 필요합니다. 환경 secret `NOTION_TOKEN`과 `api.notion.com` 접속을 사용합니다.

```bash
cd /workspace/Notion-Fitness-Tracker
python3 -m unittest discover -s tests -v
python3 -m notion plan
python3 -m notion apply
python3 -m notion verify --original .local/notion-original.json
```

- `plan`: 전체 원본 조회와 계산. 읽기 전용.
- `apply`: 보기 설정 대조, 상위 10종목 선정·주간 요약 갱신. 삭제한 보기는 `disabled_views`로 재생성을 막습니다.
- `verify`: 실제 수식·차트 필터·원본 입력값 대조. 읽기 전용.
- `live-check`: 별도 임시 DB에서 수정·삭제/복구·날짜·조건·기준 변경 검사. 종료 후 임시 페이지 삭제.

처음 연결하는 계정은 `notion inspect`로 ID와 스키마를 확인한 뒤 `notion/config.example.json`을 `.local/notion-config.json`으로 복사해 채웁니다. `notion prepare`는 검증한 원본 스키마에 `NFT …` 표시 속성을 추가합니다. `.local/`의 설정·상태·원본 덤프는 Git에서 제외됩니다.

## 갱신과 비교

원본 직접 연결 차트는 Notion에서 재계산됩니다. 최근 12주는 이동 기간, 이번 주는 월요일 시작입니다. **홈 요약, 상위 10종목 선정과 주간 요약 표는 `apply` 실행 시 갱신**됩니다. 홈 요약은 원본 기록의 월요일 주간 집계이며, 변경 후 갱신이 필요합니다. 연결 목록과 상세 차트는 원본을 직접 읽습니다.

조건 미확인 수행은 관측치로 표시합니다. 발전 지수는 `NFT Measurement Condition`·`NFT Condition Confirmed`와 유효한 기준 세트·값·버전이 필요합니다. 현재 데이터가 없는 C10 비교 차트는 삭제했습니다. 조건·기준을 확인한 뒤 `disabled_views`에서 C10·C10P·T04를 해제하면 다시 생성할 수 있습니다.

선택 명령 `notion watch --interval 90`은 실행 중에만 반복하며 차트의 기본 필터도 재적용합니다. 개인 필터를 조정할 때는 종료하세요.

API 버전은 `2026-03-11`입니다. 사용자 제공 iPhone 화면에서 큰 숫자 카드·가로 표의 문제를 확인해 홈을 요약·목록으로 수정했습니다. 수정 후 실제 기기 화면은 아직 확인하지 않았습니다.
