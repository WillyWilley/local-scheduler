# 업무 스케줄 (Local Gantt Schedule App)

**프로그램 하나를 켜면 일정이 바로 보이고, 그 자리에서 직관적으로 편집까지 되는 로컬 일정 관리 앱**입니다.
서버도, 계정도, 구독도 없습니다 — 데이터는 내 PC의 JSON 파일 하나에만 저장됩니다.

> 이 프로젝트는 AI 코딩 에이전트(Claude Code)와의 협업으로 만들어졌습니다.
> 제품 헌장([CLAUDE.md](CLAUDE.md))과 백로그([ROADMAP.md](ROADMAP.md))를 기준으로
> AI가 스스로 판단·구현·검증하며 발전시키는 "하네스 엔지니어링" 방식으로 개발되었습니다.

## 특징

- **원클릭 실행**: `업무스케줄.bat` 더블클릭 → 전체화면 앱 창 (콘솔 창 없음, 백그라운드 상주로 재실행 즉시)
- **보기 = 편집**: 간트차트에서 바 드래그(날짜 이동), 끝 잡기(기간), 더블클릭(상세 편집), 행 hover + 버튼(추가)
- **달력 범위 선택**: 편집창에서 시작일~종료일을 달력 클릭으로 선택, 사이 기간이 색으로 표시
- **완료 관리**: 상태 뱃지 클릭 한 번으로 완료 토글, 완료 항목 자동 하단 정렬, 지난 일정 자동 보관함
- **한국 공휴일 표시**, 주말 제외 근무일 계산, 다크 모드, 검색, 주간 브리핑(복사 버튼), Ctrl+Z 실행취소
- **안전한 저장**: 검증 → 자동 백업(30개) → 원자적 쓰기 → 공유용 정적 HTML 재빌드. 오래된 창의 저장이 최신 데이터를 덮어쓰지 않도록 낙관적 잠금 적용
- **공유용 정적 뷰어**: 저장할 때마다 `업무_스케줄.html`이 생성되어 NAS/공유폴더로 팀원과 보기 전용 공유 가능

## 요구 사항

- Windows 10 이상
- Python 3.9 이상 (표준 라이브러리만 사용 — `pip install` 불필요)
- Edge 또는 Chrome (앱 모드 창)

## 시작하기

```
1. 이 저장소를 다운로드 (Code → Download ZIP 또는 git clone)
2. 업무스케줄.bat 더블클릭
   → 첫 실행 시 예시 데이터(schedule_data.sample.json)로 시작됩니다
3. 화면에서 바로 편집하고 우측 상단 [저장]
```

부팅 시 자동 실행을 원하면: `Win+R` → `shell:startup` → 이 폴더의 `업무스케줄.bat` 바로가기를 넣으세요.

## 구조

```
업무스케줄.bat               실행 진입점 (pythonw로 조용히 실행)
app/
  schedule_web_editor.py    로컬 서버 + 보기/편집 통합 앱 (127.0.0.1 전용)
  build_schedule.py         data → output/업무_스케줄.html 정적 빌드
  validate_schedule.py      데이터 검증 (저장 게이트)
data/
  schedule_data.json        단일 진실 소스 (첫 실행 시 sample에서 자동 생성)
  backups/                  저장 시 자동 스냅샷 (최근 30개)
output/                     빌드 산출물 (보기 전용 HTML)
logs/                       실행 로그
CLAUDE.md / ROADMAP.md      AI 하네스: 제품 헌장과 백로그
```

데이터 형식은 사람이 읽을 수 있는 JSON입니다:

```json
{
  "sections": [
    { "type": "project", "projects": [ { "sub_projects": [ { "tasks": [
        { "title": "자료 조사", "start": "2026-07-01", "duration": 5 }
    ] } ] } ] },
    { "type": "event", "events": [
        { "title": "킥오프 회의", "start": "2026-07-02", "duration": 1, "time": "10:00~11:00" }
    ] }
  ]
}
```

- 장기 업무의 `duration`은 **주말 제외 근무일**, 일회성 일정은 **달력일** 기준
- 부모 일정은 자식 범위로 자동 계산(롤업)
- `done: true`로 날짜와 무관한 완료 처리, `status: "undetermined"`로 미정 표시

## 라이선스

- 이 저장소의 코드: [MIT License](LICENSE)
- 간트차트 렌더링은 [dhtmlxGantt](https://dhtmlx.com/docs/products/dhtmlxGantt/)를 CDN으로 불러 사용합니다.
  dhtmlxGantt Standard는 GPLv2로 배포되므로, 이 앱을 **개인/사내 도구로 사용하는 것은 자유**지만
  dhtmlxGantt를 포함해 상용 제품으로 재배포하려면 별도 라이선스 확인이 필요합니다.
