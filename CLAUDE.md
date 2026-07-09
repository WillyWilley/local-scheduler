# 업무 스케줄 앱 — 제품 헌장

## 비전
**프로그램 하나(업무스케줄.bat)를 켜면 스케줄이 바로 보이고, 그 자리에서 직관적으로 편집까지 되는 앱.**
깔끔한 UI가 최우선 가치다. AI(Claude)가 이 헌장을 기준으로 스스로 판단하며 지속적으로 발전시킨다.

## 아키텍처 (폴더 트리 + 데이터 흐름)
```
업무스케줄.bat  →  app/schedule_web_editor.py (로컬 서버 + 앱 창)
                     │  보기+편집 통합 화면 (dhtmlxgantt)
                     │  저장 = 검증 → 백업 → JSON 쓰기 → 재빌드
                     ▼
              data/schedule_data.json  ←── 단일 진실 소스
                     │
              app/build_schedule.py    ──→ output/업무_스케줄.html (정적 뷰어, 부산물)
```
- `app/` — 코드 전부 (`validate_schedule.py` = 저장 게이트, `schedule_editor.py` = 레거시 tkinter 에디터 삭제 금지, `export_github.py` = 공개용 내보내기)
- `data/` — 데이터 (`schedule_data.sample.json` = 첫 실행용 예시, `backups/` = 저장 직전 스냅샷 30개)
- `assets/` — 차트 라이브러리 (없으면 CDN 폴백), `logs/` — 실행 로그, `output/` — 빌드 산출물 (git 추적 제외)

## 데이터 규칙
- 날짜는 `YYYY-MM-DD` (패딩 필수), 계층: `sections[] > projects[] > sub_projects[] > tasks[]` / `events[]`
- `duration`: 장기 업무는 **주말 제외 근무일 수**, 일회성 일정(events)은 **주말 포함 달력일 수** (둘 다 시작일 포함)
- `done: true` (선택) — 날짜와 무관하게 완료 처리 (상태 뱃지 클릭으로 토글). 없으면 날짜로 자동 판별. 부모의 done은 자식에서 파생되므로 JSON에 기록하지 않는다
- `status: "undetermined"` (선택) — 미정 항목. 예상 날짜와 공존 가능하며, 라이트박스에서 날짜를 저장할 때만 해제된다
- ID는 숫자 사용. `sec-*`, `*-past`는 시스템 예약
- 자식이 있는 항목의 일정은 자식 범위로 롤업 — 부모에 직접 날짜를 쓰지 않는다
- HTML 파일은 빌드 산출물이다. **직접 수정 금지**, 반드시 JSON을 고치고 빌드할 것

## 불변 규칙 (어길 수 없음)
1. 저장 경로는 항상 `검증 → 백업 → JSON 쓰기 → 재빌드` 순서. 이 순서를 깨는 코드 금지
2. 기능 삭제·데이터 스키마 변경은 사용자 확인 후에만
3. UI 원칙: 요소를 더하기보다 정돈한다. 기존 팔레트(인디고 계열 + 프로젝트 색상) 유지. 새 버튼·패널은 신중히
4. 파이썬 표준 라이브러리만 사용 (추가 설치 없이 동작해야 함)

## 변경 후 검증 (완료의 정의)
```
python -m py_compile build_schedule.py schedule_web_editor.py validate_schedule.py
python validate_schedule.py          # 데이터 검증 통과
python build_schedule.py             # 경고 0으로 빌드 성공
```
+ 왕복 테스트: 편집 없이 저장 시 원본 보존(순서 재배열·빈 필드 정리만 허용)
+ UI 변경 시: 실제 화면을 띄워 스크린샷으로 확인 후 완료 선언

## 개선 사이클 (하네스 동작 방식)
- `ROADMAP.md`가 백로그다. 사용자가 "개선 돌려"라고 하면:
  1. ROADMAP에서 가장 가치 있는 항목 선택 (사용자 피드백 > 버그 > UI 다듬기 > 신기능)
  2. 구현 → 위의 검증 절차 통과 → ROADMAP 갱신 (완료 이동 + 새 아이디어 추가)
  3. `git commit` (한국어 메시지, 작업 단위마다)
- 사용자가 던진 불편사항은 즉시 ROADMAP 최상단에 등록
- UI 취향이 갈리는 변경은 스크린샷을 보여주고 방향 확인 후 진행
