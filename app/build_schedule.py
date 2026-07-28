# -*- coding: utf-8 -*-
"""
업무 스케줄 빌드 스크립트
schedule_data.json → 업무_스케줄.html 생성

사용법: python build_schedule.py
"""
import json
import os
import sys
from datetime import datetime, timedelta

if getattr(sys, "frozen", False):
    # exe 배포본(PyInstaller): data/output 경로가 exe 옆 기준
    SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.executable))
    BASE_DIR = SCRIPT_DIR
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))   # app/
    BASE_DIR = os.path.dirname(SCRIPT_DIR)                     # 프로젝트 루트
DATA_FILE = os.path.join(BASE_DIR, "data", "schedule_data.json")
SAMPLE_FILE = os.path.join(BASE_DIR, "data", "schedule_data.sample.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "output", "업무_스케줄.html")


def ensure_data_file():
    """데이터 파일이 없으면 예시 데이터로 시작 (처음 받아서 실행하는 사람용).
    기존 파일에 반복 일정 섹션이 없으면 추가한다 (스키마 마이그레이션)."""
    if not os.path.exists(DATA_FILE) and os.path.exists(SAMPLE_FILE):
        import shutil
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        shutil.copyfile(SAMPLE_FILE, DATA_FILE)
        print("schedule_data.json이 없어 예시 데이터로 시작합니다.")

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    if ensure_sections(data):
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)
        print("반복 일정 섹션을 추가했습니다.")

HOLIDAY_CACHE = os.path.join(BASE_DIR, "data", "holidays_cache.json")

# 한국 공휴일 {날짜: 이름} — 내장 폴백 데이터.
# 실제 표시는 load_holidays()가 [자동 갱신 캐시 > 내장] 순으로 병합해 사용.
HOLIDAYS_BUILTIN = {
    "2026-01-01": "신정",
    "2026-02-16": "설날", "2026-02-17": "설날", "2026-02-18": "설날",
    "2026-03-01": "삼일절", "2026-03-02": "대체휴일",
    "2026-05-05": "어린이날",
    "2026-05-24": "석탄일", "2026-05-25": "대체휴일",
    "2026-06-06": "현충일",
    "2026-08-15": "광복절", "2026-08-17": "대체휴일",
    "2026-09-24": "추석", "2026-09-25": "추석", "2026-09-26": "추석", "2026-09-28": "대체휴일",
    "2026-10-03": "개천절", "2026-10-05": "대체휴일",
    "2026-10-09": "한글날",
    "2026-12-25": "성탄절",
    "2027-01-01": "신정",
    "2027-02-06": "설날", "2027-02-07": "설날", "2027-02-08": "설날", "2027-02-09": "대체휴일",
    "2027-03-01": "삼일절",
    "2027-05-05": "어린이날",
    "2027-05-13": "석탄일",
    "2027-06-06": "현충일",
    "2027-08-15": "광복절", "2027-08-16": "대체휴일",
    "2027-09-14": "추석", "2027-09-15": "추석", "2027-09-16": "추석",
    "2027-10-03": "개천절", "2027-10-04": "대체휴일",
    "2027-10-09": "한글날", "2027-10-11": "대체휴일",
    "2027-12-25": "성탄절", "2027-12-27": "대체휴일",
}

# API 이름이 길 때 달력 칸에 맞게 줄이는 표
HOLIDAY_SHORT_NAMES = {
    "부처님 오신 날": "석탄일",
    "부처님오신날": "석탄일",
    "기독탄신일": "성탄절",
    "크리스마스": "성탄절",
    "1월 1일": "신정",
    "새해": "신정",
    "대체 휴일": "대체휴일",
    "대체공휴일": "대체휴일",
    "제헌절": "제헌절",
}


def _short_holiday_name(name):
    name = str(name).strip()
    if name in HOLIDAY_SHORT_NAMES:
        return HOLIDAY_SHORT_NAMES[name]
    return name[:5] if len(name) > 5 else name


def load_holidays():
    """공휴일 병합: 자동 갱신 캐시가 있으면 내장 데이터 위에 덮어쓴다."""
    merged = dict(HOLIDAYS_BUILTIN)
    try:
        with open(HOLIDAY_CACHE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        merged.update(cache.get("holidays", {}))
    except (OSError, ValueError):
        pass
    return merged


def day_note_map(data):
    """day_notes 배열 → {키: 메모} 맵. 빈 메모는 버린다.
    키는 "날짜"(날짜 전체 메모) 또는 "날짜|항목ID"(특정 항목의 그 날짜 메모)."""
    out = {}
    for note in data.get("day_notes") or []:
        if not isinstance(note, dict):
            continue
        date = str(note.get("date") or "").strip()
        text = str(note.get("text") or "").strip()
        item = note.get("item_id")
        if not (date and text):
            continue
        key = date if item in (None, "") else "%s|%s" % (date, item)
        out[key] = text
    return out


def refresh_holidays(force=False):
    """공휴일 자동 갱신 (무료 공개 API, 키 불필요).
    캐시가 없거나 30일 넘게 오래됐거나 내년 데이터가 빠졌을 때만 네트워크 호출.
    실패해도 캐시/내장 데이터로 동작하므로 오프라인에서도 안전."""
    import urllib.request

    today = datetime.now()
    try:
        with open(HOLIDAY_CACHE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        fetched = datetime.strptime(cache.get("fetched", "2000-01-01"), "%Y-%m-%d")
        has_next_year = any(k.startswith(str(today.year + 1)) for k in cache.get("holidays", {}))
        if not force and has_next_year and (today - fetched).days < 30:
            return False  # 아직 신선함
    except (OSError, ValueError):
        pass  # 캐시 없음 → 받아온다

    holidays = {}
    for year in range(today.year - 1, today.year + 3):
        url = f"https://date.nager.at/api/v3/PublicHolidays/{year}/KR"
        with urllib.request.urlopen(url, timeout=10) as r:
            for item in json.loads(r.read().decode("utf-8")):
                date = item.get("date")
                types = item.get("types") or ["Public"]
                if date and "Public" in types:
                    holidays[date] = _short_holiday_name(item.get("localName") or item.get("name") or "공휴일")

    if len(holidays) < 10:  # 응답이 비정상적으로 빈약하면 캐시를 덮지 않음
        raise ValueError(f"공휴일 응답이 너무 적음 ({len(holidays)}건)")

    os.makedirs(os.path.dirname(HOLIDAY_CACHE), exist_ok=True)
    tmp = HOLIDAY_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"fetched": today.strftime("%Y-%m-%d"), "holidays": holidays},
                  f, ensure_ascii=False, indent=2)
    os.replace(tmp, HOLIDAY_CACHE)
    print(f"공휴일 데이터 자동 갱신: {len(holidays)}건 ({today.year - 1}~{today.year + 2}년)")
    return True


def iso_to_gantt(date_str: str, time: str = "00:00") -> str:
    """YYYY-MM-DD → DD-MM-YYYY HH:MM (dhtmlxgantt 형식)"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return d.strftime("%d-%m-%Y") + " " + time


def workday_end_date(start_dt: datetime, work_days: int) -> datetime:
    """시작일부터 주말(토·일) 제외 work_days일째 되는 날.
    시작일은 주말이어도 항상 1일째로 포함."""
    d = start_dt
    count = 1
    while count < int(work_days):
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d


def workdays_between(start_dt: datetime, end_dt: datetime) -> int:
    """시작~끝(포함) 중 주말(토·일) 제외 일수 (시작일은 항상 포함)"""
    count = 1
    d = start_dt
    while d < end_dt:
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return count


def gantt_date(d: datetime) -> str:
    return d.strftime("%d-%m-%Y 00:00")


# ══════════════════════ 반복 일정 ══════════════════════
# 규칙 예:
#   매주:  {"freq":"weekly",  "interval":1, "weekdays":[1,5]}   (0=일 … 6=토)
#   매월:  {"freq":"monthly", "day":15}  또는  {"freq":"monthly","nth":1,"weekday":1}
#          (nth: 1~4=첫째~넷째, -1=마지막)
#   매년:  {"freq":"yearly",  "month":3, "day":2}
MAX_OCCURRENCES = 400  # 폭주 방지 상한
RECUR_MAX_YEARS = 3    # 종료일이 없으면 오늘 기준 3년까지만 전개


def _nth_weekday_of_month(year, month, nth, weekday):
    """그 달의 nth번째 weekday(0=일). nth=-1이면 마지막 주. 없으면 None."""
    first = datetime(year, month, 1)
    days_in_month = (datetime(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)).day
    matches = [d for d in range(1, days_in_month + 1)
               if datetime(year, month, d).weekday() == (weekday - 1) % 7]
    if not matches:
        return None
    if nth == -1:
        return datetime(year, month, matches[-1])
    if 1 <= nth <= len(matches):
        return datetime(year, month, matches[nth - 1])
    return None


def expand_recurrence(rule):
    """반복 규칙 → 발생 날짜(datetime) 목록"""
    freq = rule.get("freq", "weekly")
    try:
        start = datetime.strptime(rule["start"], "%Y-%m-%d")
    except (KeyError, ValueError):
        return []
    if rule.get("end"):
        try:
            end = datetime.strptime(rule["end"], "%Y-%m-%d")
        except ValueError:
            end = start
    else:
        end = datetime.now() + timedelta(days=365 * RECUR_MAX_YEARS)
    if end < start:
        end = start

    out = []
    if freq == "weekly":
        interval = max(1, int(rule.get("interval", 1) or 1))
        # weekday: 파이썬 0=월 … 6=일 / 데이터 0=일 … 6=토
        wanted = {(int(w) - 1) % 7 for w in (rule.get("weekdays") or [])}
        if not wanted:
            wanted = {start.weekday()}
        # 시작 주의 월요일 기준으로 interval 주 간격만 채택
        week0 = start - timedelta(days=start.weekday())
        d = start
        while d <= end and len(out) < MAX_OCCURRENCES:
            weeks = (d - timedelta(days=d.weekday()) - week0).days // 7
            if d.weekday() in wanted and weeks % interval == 0:
                out.append(d)
            d += timedelta(days=1)

    elif freq == "monthly":
        y, m = start.year, start.month
        while len(out) < MAX_OCCURRENCES:
            if rule.get("nth"):
                d = _nth_weekday_of_month(y, m, int(rule["nth"]), int(rule.get("weekday", 1)))
            else:
                day = int(rule.get("day", start.day))
                try:
                    d = datetime(y, m, day)
                except ValueError:
                    d = None  # 그 달에 없는 날짜(예: 31일) → 건너뜀
            if d and start <= d <= end:
                out.append(d)
            if d and d > end:
                break
            m += 1
            if m > 12:
                m, y = 1, y + 1
            if datetime(y, m, 1) > end:
                break

    elif freq == "yearly":
        month = int(rule.get("month", start.month))
        day = int(rule.get("day", start.day))
        for y in range(start.year, end.year + 1):
            try:
                d = datetime(y, month, day)
            except ValueError:
                continue
            if start <= d <= end:
                out.append(d)
            if len(out) >= MAX_OCCURRENCES:
                break

    return out


def recur_summary(rule):
    """규칙 → 사람이 읽는 한 줄 요약"""
    wd = ["일", "월", "화", "수", "목", "금", "토"]
    freq = rule.get("freq", "weekly")
    if freq == "weekly":
        iv = int(rule.get("interval", 1) or 1)
        head = "매주" if iv == 1 else ("격주" if iv == 2 else f"{iv}주마다")
        days = "".join(wd[int(w) % 7] for w in sorted(rule.get("weekdays") or []))
        return f"{head} {days}" if days else head
    if freq == "monthly":
        if rule.get("nth"):
            nth = int(rule["nth"])
            label = "마지막" if nth == -1 else ["첫째", "둘째", "셋째", "넷째"][min(nth, 4) - 1]
            return f"매월 {label} {wd[int(rule.get('weekday', 1)) % 7]}요일"
        return f"매월 {int(rule.get('day', 1))}일"
    if freq == "yearly":
        return f"매년 {int(rule.get('month', 1))}월 {int(rule.get('day', 1))}일"
    return "반복"


def ensure_sections(data):
    """오래된 데이터에 반복 일정 섹션이 없으면 추가 (마이그레이션)"""
    if not any(s.get("type") == "recurring" for s in data.get("sections", [])):
        data.setdefault("sections", []).append({
            "id": "sec-recur",
            "title": "🔁 반복 일정",
            "type": "recurring",
            "recurrences": [],
        })
        return True
    return False


def build_gantt_data(data: dict) -> list:
    """계층 JSON → dhtmlxgantt 플랫 배열 변환

    바 표시 규칙(멘탈 모델): 날짜는 잎(업무·이벤트·자식 없는 항목)에만 입력한다.
    자식이 있는 프로젝트/세부 프로젝트는 자식 범위로 자동 계산(롤업)되며,
    직접 입력된 일정은 무시된다. 전부 완료된 가지는 접힌 상태로 표시.
    """
    items = []
    today = datetime.now().date()

    def leaf_span(obj):
        """start/duration이 입력된 항목의 (시작일, 마지막날) datetime 쌍"""
        if obj.get("start") and obj.get("duration"):
            s = datetime.strptime(obj["start"], "%Y-%m-%d")
            return s, workday_end_date(s, obj["duration"])
        return None

    def apply_span(item, span):
        s, e = span
        item["start_date"] = gantt_date(s)
        item["end_date"] = gantt_date(e + timedelta(days=1))  # gantt end_date는 배타적
        item["work_days"] = workdays_between(s, e)

    def apply_rollup(item, spans):
        """자식 범위들의 합집합으로 부모 일정 계산
        (접기·완료 표시는 호출부에서 is_done 기준으로 결정)"""
        s = min(sp[0] for sp in spans)
        e = max(sp[1] for sp in spans)
        apply_span(item, (s, e))
        item["done_count"] = sum(1 for sp in spans if sp[1].date() < today)
        item["child_count"] = len(spans)
        return s, e

    def obj_last_day(obj):
        """항목(자식 포함)의 가장 늦은 종료일. 미정 자식은 제외. 날짜 없으면 None."""
        ends = []
        for t in obj.get("tasks", []):
            if t.get("status") == "undetermined":
                continue
            sp = leaf_span(t)
            if sp:
                ends.append(sp[1])
        for s in obj.get("sub_projects", []):
            if s.get("status") == "undetermined":
                continue
            e = obj_last_day(s)
            if e:
                ends.append(e)
        if not ends:
            sp = leaf_span(obj)
            if sp:
                ends.append(sp[1])
        return max(ends) if ends else None

    def is_done(obj):
        """완료 여부: done 플래그가 있거나 마지막 종료일이 오늘 이전.
        자식이 있으면 (미정 제외한) 자식 전부 완료 시 완료. 미정 자식이 하나라도
        있으면 아직 끝난 가지가 아니므로 미완료. (날짜 없음도 미완료 취급)"""
        if obj.get("status") == "undetermined":
            return False
        all_kids = list(obj.get("tasks", [])) + list(obj.get("sub_projects", []))
        if all_kids:
            if any(k.get("status") == "undetermined" for k in all_kids):
                return False
            return all(is_done(k) for k in all_kids)
        if obj.get("done"):
            return True
        e = obj_last_day(obj)
        return e is not None and e.date() < today

    def done_last(objs):
        """미완료 먼저, 완료는 아래로 (같은 그룹 안에서는 원래 순서 유지)"""
        return sorted(objs, key=is_done)

    for section in data["sections"]:
        sec_id = section["id"]
        sec_type = section["type"]

        # 섹션 헤더
        items.append({
            "id": sec_id,
            "text": section["title"],
            "type": "project",
            "open": True,
            "is_section": sec_type,
            "unscheduled": True,
        })

        if sec_type == "project":
            for proj in done_last(section.get("projects", [])):
                color = proj.get("color", "")
                color_class = f"color-{color}" if color else ""

                proj_item = {
                    "id": proj["id"],
                    "text": proj["title"],
                    "parent": sec_id,
                    "open": True,
                    "color_class": color_class,
                    "is_parent_project": True,
                }
                if proj.get("notes"):
                    proj_item["notes"] = proj["notes"]
                proj_children = []
                proj_spans = []

                for sub in done_last(proj.get("sub_projects", [])):
                    sub_item = {
                        "id": sub["id"],
                        "text": sub["title"],
                        "parent": proj["id"],
                        "open": True,
                        "is_sub_project": True,
                        "color_class": color_class,
                    }
                    if sub.get("notes"):
                        sub_item["notes"] = sub["notes"]

                    task_items = []
                    task_spans = []
                    for task in done_last(sub.get("tasks", [])):
                        t_item = {
                            "id": task["id"],
                            "text": task["title"],
                            "parent": sub["id"],
                            "progress": task.get("progress", 0),
                            "color_class": color_class,
                            "bar_level": 3,
                        }
                        t_span = leaf_span(task)
                        if t_span:
                            apply_span(t_item, t_span)
                        else:
                            t_item["unscheduled"] = True  # 저장 시 임의 날짜 부여 방지
                        if task.get("status") == "undetermined":
                            t_item["custom_status"] = "undetermined"
                        elif t_span:
                            task_spans.append(t_span)
                        if task.get("notes"):
                            t_item["notes"] = task["notes"]
                        if task.get("done"):
                            t_item["done"] = True
                        task_items.append(t_item)

                    own_span = leaf_span(sub)
                    if task_spans:
                        # 자식이 있으면 항상 롤업 — 직접 입력된 일정은 무시
                        span = apply_rollup(sub_item, task_spans)
                        sub_item["bar_level"] = 2
                        if is_done(sub):  # done 플래그·날짜 모두 반영해 접기/완료 처리
                            sub_item["open"] = False
                            sub_item["done"] = True
                        if own_span:
                            print(f"  ! '{sub['title']}' 직접 입력 일정 무시됨 (자식 범위로 자동 계산)")
                        proj_spans.append(span)
                        # '상자' 밴드: 세부 프로젝트 범위를 자신+자식 행에 표시
                        if color:
                            band_start = int(span[0].strftime("%Y%m%d"))
                            band_end = int(span[1].strftime("%Y%m%d"))
                            for band_item in [sub_item] + task_items:
                                band_item["band_start"] = band_start
                                band_item["band_end"] = band_end
                                band_item["band_theme"] = color
                    elif own_span:
                        # 자식이 없으면 잎으로 취급 (실색 바)
                        apply_span(sub_item, own_span)
                        sub_item["bar_level"] = 3
                        sub_item["progress"] = sub.get("progress", 0)
                        if sub.get("done"):
                            sub_item["done"] = True
                        if sub.get("status") == "undetermined":
                            sub_item["custom_status"] = "undetermined"
                        else:
                            proj_spans.append(own_span)
                    else:
                        sub_item["unscheduled"] = True
                        if sub.get("status") == "undetermined" or sub.get("tasks"):
                            # 직접 미정 지정 또는 자식 전부 미정이면 미정 표시
                            sub_item["custom_status"] = "undetermined"

                    proj_children.append(sub_item)
                    proj_children.extend(task_items)

                own_span = leaf_span(proj)
                if proj_spans:
                    apply_rollup(proj_item, proj_spans)
                    proj_item["bar_level"] = 1
                    if is_done(proj):
                        proj_item["open"] = False
                        proj_item["done"] = True
                    if own_span:
                        print(f"  ! '{proj['title']}' 직접 입력 일정 무시됨 (자식 범위로 자동 계산)")
                elif own_span:
                    apply_span(proj_item, own_span)
                    proj_item["bar_level"] = 3
                    proj_item["progress"] = proj.get("progress", 0)
                    if proj.get("done"):
                        proj_item["done"] = True
                    if proj.get("status") == "undetermined":
                        proj_item["custom_status"] = "undetermined"
                else:
                    proj_item["unscheduled"] = True
                    if proj.get("status") == "undetermined" or proj.get("sub_projects"):
                        proj_item["custom_status"] = "undetermined"

                items.append(proj_item)
                items.extend(proj_children)

        elif sec_type == "event":
            def build_evt_item(evt, parent_id):
                color = evt.get("color", "")
                color_class = f"color-{color}" if color else ""
                item = {
                    "id": evt["id"],
                    "text": evt["title"],
                    "parent": parent_id,
                    "start_date": iso_to_gantt(evt["start"]),
                    "work_days": evt["duration"],
                    "is_single_event": True,
                    "color_class": color_class,
                    "bar_level": 3,
                }
                if int(evt.get("duration", 1)) <= 1:
                    item["type"] = "milestone"  # 하루짜리 일정은 다이아몬드
                    # 정오로 지정해 다이아몬드가 날짜 칸 정중앙에 오도록
                    item["start_date"] = iso_to_gantt(evt["start"], "12:00")
                else:
                    # 일회성 일정은 주말 포함 달력일 기준 (행사는 주말에도 열림)
                    s = datetime.strptime(evt["start"], "%Y-%m-%d")
                    e = s + timedelta(days=int(evt["duration"]) - 1)
                    apply_span(item, (s, e))
                if evt.get("time"):
                    item["custom_time"] = evt["time"]
                if evt.get("notes"):
                    item["notes"] = evt["notes"]
                if evt.get("done"):
                    item["done"] = True
                return item

            def evt_last_day(evt):
                s = datetime.strptime(evt["start"], "%Y-%m-%d")
                return s + timedelta(days=(int(evt.get("duration", 1)) or 1) - 1)

            def evt_done(evt):
                return bool(evt.get("done")) or evt_last_day(evt).date() < today

            events = section.get("events", [])
            upcoming = [e for e in events if not evt_done(e)]
            past = [e for e in events if evt_done(e)]

            upcoming.sort(key=lambda e: (e["start"], e.get("time", "")))  # 가까운 일정부터
            for evt in upcoming:
                items.append(build_evt_item(evt, sec_id))

            # 지나간 일정은 접힌 그룹으로 모아 목록 맨 아래에 표시
            if past:
                past.sort(key=lambda e: e["start"], reverse=True)  # 최근 것부터
                past_id = f"{sec_id}-past"
                items.append({
                    "id": past_id,
                    "text": f"🗂 지난 일정 ({len(past)})",
                    "parent": sec_id,
                    "type": "project",
                    "open": False,
                    "is_past_group": True,
                    "unscheduled": True,
                })
                for evt in past:
                    items.append(build_evt_item(evt, past_id))

        elif sec_type == "recurring":
            for rec in section.get("recurrences", []):
                color = rec.get("color", "")
                occ = expand_recurrence(rec)
                item = {
                    "id": rec["id"],
                    "text": rec["title"],
                    "parent": sec_id,
                    "is_recur": True,
                    "bar_level": 3,
                    "color_class": f"color-{color}" if color else "",
                    "recur": {k: v for k, v in rec.items()
                              if k in ("freq", "interval", "weekdays", "day", "nth", "weekday", "month")},
                    "recur_text": recur_summary(rec),
                    "occurrences": [d.strftime("%Y-%m-%d") for d in occ],
                    "recur_open": not rec.get("end"),  # 종료일 없음 = 무기한 반복
                }
                # 규칙 기간을 행의 범위로 (바는 숨기고 회차 다이아몬드만 그림)
                s = datetime.strptime(rec["start"], "%Y-%m-%d")
                e = (datetime.strptime(rec["end"], "%Y-%m-%d")
                     if rec.get("end") else (occ[-1] if occ else s))
                item["start_date"] = gantt_date(s)
                item["end_date"] = gantt_date(e + timedelta(days=1))
                if rec.get("time"):
                    item["custom_time"] = rec["time"]
                if rec.get("notes"):
                    item["notes"] = rec["notes"]
                items.append(item)

    return items


def hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def build_color_css(colors: dict) -> str:
    """색상 정의 → CSS 문자열 (<style> 태그 안에 삽입용)

    레벨별 바 스타일: 업무/이벤트=실색, 세부 프로젝트=반투명 채움+실선 테두리,
    프로젝트=진한색 브래킷(양끝 캡은 ::before/::after)"""
    lines = []
    for name, c in colors.items():
        cls = f"color-{name}"
        bg, border = c["bg"], c["border"]
        lines.append(f"  .gantt_task_line.{cls} {{ background: {bg} !important; border-color: {border} !important; }}")
        # 세부 프로젝트: 실색 채움 + inset shadow 윤곽선
        lines.append(f"  .gantt_task_line.{cls}.lv-subproject {{ background: {bg} !important; box-shadow: inset 0 0 0 2px {border}; }}")
        # 프로젝트: 진한 실색
        lines.append(f"  .gantt_task_line.{cls}.lv-project {{ background: {border} !important; border-color: {border} !important; }}")
        # 반복 일정 회차 다이아몬드
        lines.append(f"  .recur-dot.{cls} {{ background: {bg}; }}")
        # '상자' 밴드: 세부 프로젝트 범위만큼 자식 행 배경을 연하게 물들임
        lines.append(f"  .gantt_task_cell.range-band.band-{name} {{ background: {hex_to_rgba(bg, 0.07)}; }}")
        lines.append(f"  .gantt_task_cell.weekend.range-band.band-{name} {{ background: {hex_to_rgba(bg, 0.13)} !important; }}")
    return "\n".join(lines)


HTML_TEMPLATE = r'''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>업무 스케줄</title>
<script src="https://cdn.dhtmlx.com/gantt/edge/dhtmlxgantt.js"></script>
<link href="https://cdn.dhtmlx.com/gantt/edge/dhtmlxgantt.css" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
  html, body {
    margin: 0; padding: 0; height: 100%; overflow: hidden;
    font-family: 'Noto Sans KR', sans-serif;
    background: #f5f7fa;
  }

  .page-header {
    background: #fff;
    border-bottom: 1px solid #e5e7eb;
    padding: 14px 28px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    height: 64px;
    box-sizing: border-box;
  }

  .page-header h1 { font-size: 20px; font-weight: 700; color: #1a1a2e; margin: 0; }
  .page-header .subtitle { font-size: 12px; color: #6b7280; margin-top: 2px; }

  .header-controls { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }

  .header-controls button {
    padding: 6px 14px; border: 1px solid #e5e7eb; border-radius: 6px;
    background: #fff; color: #374151; font-family: inherit;
    font-size: 12px; font-weight: 500; cursor: pointer; transition: all 0.15s;
  }
  .header-controls button:hover { border-color: #4f46e5; color: #4f46e5; }
  .header-controls button.active { background: #4f46e5; color: #fff; border-color: #4f46e5; }

  #gantt_here { width: 100%; height: calc(100% - 64px); }

  .gantt_container { font-family: 'Noto Sans KR', sans-serif !important; }
  .gantt_grid_head_cell { font-weight: 600 !important; font-size: 12px !important; color: #6b7280 !important; }
  .gantt_cell, .gantt_tree_content { font-size: 13px !important; }

  .project-row { font-weight: 600 !important; background: #f8f9fb !important; }
  .sub-project-row { font-weight: 500 !important; }
  .undetermined-row { opacity: 0.55; font-style: italic; }

  .section-row { background: #eef2ff !important; font-weight: 700 !important; border-top: 2px solid #c7d2fe !important; }
  .section-row .gantt_tree_content { color: #4338ca !important; font-size: 14px !important; letter-spacing: 0.5px; }
  .section-row-event { background: #fef3c7 !important; font-weight: 700 !important; border-top: 2px solid #fcd34d !important; }
  .section-row-event .gantt_tree_content { color: #92400e !important; font-size: 14px !important; letter-spacing: 0.5px; }
  .section-row-recur { background: #ecfdf5 !important; font-weight: 700 !important; border-top: 2px solid #6ee7b7 !important; }
  .section-row-recur .gantt_tree_content { color: #047857 !important; font-size: 14px !important; letter-spacing: 0.5px; }

  /* 반복 일정: 바는 숨기고 회차마다 다이아몬드 */
  .recur-row .gantt_tree_content { padding-left: 10px !important; }
  .recur-dot {
    position: absolute; width: 11px; height: 11px;
    background: #6b7280; transform: rotate(45deg); border-radius: 2px;
    pointer-events: none; z-index: 1;
  }
  .status-recur { background: #d1fae5; color: #047857; }

  .single-event-row .gantt_tree_content { padding-left: 10px !important; }

  /* 지난 일정 그룹 (접힌 보관함) */
  .past-group-row { background: #f9fafb !important; }
  .past-group-row .gantt_tree_content { color: #9ca3af !important; font-size: 12px !important; font-weight: 500; }

  /* 날짜 메모: 눈금에는 메모지 표식만, 내용은 클릭해야 보인다 */
  .day-note-mark {
    position: absolute; top: 1px; right: 2px;
    font-size: 10px; line-height: 1; cursor: pointer; opacity: 0.85;
    transition: transform 0.12s;
  }
  .day-note-mark:hover { opacity: 1; transform: scale(1.3); }
  /* 항목 메모: 그 항목 행의 그 날짜 칸 하나에만 메모지가 남는다 (클릭하면 내용 보기) */
  body.scale-day .gantt_task_cell { position: relative; }
  body.scale-day .gantt_task_cell.day-note-cell { cursor: pointer; }
  body.scale-day .gantt_task_cell.day-note-cell::after {
    content: "📝"; position: absolute; inset: 0;
    display: flex; align-items: center; justify-content: center;
    font-size: 12px; background: rgba(251, 191, 36, 0.30);
    box-shadow: inset 0 0 0 1px rgba(217, 119, 6, 0.5);
    pointer-events: none; z-index: 5;  /* 막대 위에서도 보이도록 */
  }
  /* hover 미리보기 툴팁 (클릭 없이 내용 확인, 마우스를 가리지 않도록 통과) */
  .day-note-tip {
    position: fixed; z-index: 39; max-width: 300px; display: none;
    background: #fff; border: 1px solid #e5e7eb; border-radius: 8px;
    box-shadow: 0 6px 20px rgba(0,0,0,0.14); padding: 8px 10px;
    pointer-events: none;
  }
  .day-note-tip.show { display: block; }
  .day-note-tip .dn-tip-title { font-size: 11.5px; color: #4f46e5; font-weight: 700; margin-bottom: 4px; }
  .day-note-tip .dn-tip-text {
    font-size: 12.5px; color: #374151; white-space: pre-wrap;
    word-break: break-word; max-height: 160px; overflow: hidden;
  }
  .day-note-pop {
    position: fixed; z-index: 40; width: 264px; display: none;
    background: #fff; border: 1px solid #e5e7eb; border-radius: 10px;
    box-shadow: 0 10px 30px rgba(0,0,0,0.16); padding: 12px;
  }
  .day-note-pop.show { display: block; }
  .day-note-pop h4 { margin: 0 0 8px; font-size: 12.5px; color: #4f46e5; font-weight: 700; }
  .day-note-pop .dn-text {
    font-size: 13px; color: #374151; white-space: pre-wrap;
    word-break: break-word; max-height: 220px; overflow: auto;
  }

  .gantt_task_line.hide-bar { display: none !important; }
  .section-row .gantt_task_line { display: none !important; }

  /* ── 3단 바 위계 (두께는 전부 동일, 채움 방식으로만 구분):
     프로젝트=진한 실색·각진 모서리 / 세부=윤곽선+연한 채움 / 업무=실색·둥근 모서리 ── */
  .gantt_task_line { box-sizing: border-box !important; }

  .gantt_task_line.lv-project {
    border-radius: 2px;
    border-width: 0;
    background: #6b7280;
  }
  .gantt_task_line.lv-project .gantt_task_progress_wrapper,
  .gantt_task_line.lv-project .gantt_task_content { display: none; }

  /* 세부 프로젝트: 테두리는 inset shadow로 그려 실색 바와 픽셀 폭 완전 일치 */
  .gantt_task_line.lv-subproject {
    border-radius: 3px; border: none !important;
    background: #9ca3af;
    box-shadow: inset 0 0 0 2px #6b7280;
  }
  .gantt_task_line.lv-subproject .gantt_task_progress_wrapper { display: none; }

  .gantt_task_line.lv-task { border-radius: 4px; }

  .gantt_side_content {
    font-size: 11px; color: #6b7280;
    max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }

  /* ── 완료 항목: 뒤로 물러남 (형태 유지, 채도·불투명도만 낮춤) ── */
  .gantt_task_line.is-done { filter: grayscale(0.7); opacity: 0.58; }
  .gantt_row.done-row .gantt_tree_content,
  .gantt_row.done-row .gantt_cell { color: #9ca3af !important; }
  .gantt_row.done-row.leaf-row .gantt_tree_content { text-decoration: line-through; }

  .weekend { background: #eceef2 !important; }
  .gantt_scale_cell.weekend { font-weight: 600; }
  .gantt_scale_cell.weekend.saturday { color: #2563eb !important; }
  .gantt_scale_cell.weekend.sunday { color: #dc2626 !important; }
  .gantt_scale_cell.holiday { background: rgba(220,38,38,0.08) !important; color: #dc2626 !important; font-weight: 600; }
  .gantt_scale_cell.holiday .hd { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; line-height: 1.2; }
  .gantt_scale_cell.holiday .hd-d { font-size: 12px; font-weight: 700; }
  .gantt_scale_cell.holiday .hd-n { font-size: 9px; letter-spacing: -0.5px; white-space: nowrap; font-weight: 600; }
  .gantt_task_cell.holiday { background: rgba(220,38,38,0.06) !important; }
  .gantt_task_cell.holiday.range-band { background: rgba(220,38,38,0.10) !important; }
  .gantt_task_cell.today { background: #e0e7ff !important; }

  .status-badge {
    display: inline-block; font-size: 10px; padding: 1px 6px;
    border-radius: 10px; font-weight: 500; margin-left: 4px; vertical-align: middle;
  }
  .status-in-progress { background: #dbeafe; color: #1d4ed8; }
  .status-upcoming { background: #f3f4f6; color: #6b7280; }
  .status-undetermined { background: #fef3c7; color: #92400e; }

  /* 프로젝트별 색상 (빌드 시 자동 생성) */
{{COLOR_CSS}}
</style>
</head>
<body>

<div class="page-header">
  <div>
    <h1>업무 스케줄</h1>
    <div class="subtitle">마지막 업데이트: {{LAST_UPDATED}}
      · 만든이 <a class="made-by" href="mailto:cmlee@kaeri.re.kr" style="color:inherit;text-decoration:none">cmlee@kaeri.re.kr</a></div>
  </div>
  <div class="header-controls">
    <button data-scale="day" class="active">일간</button>
    <button data-scale="week">주간</button>
    <button data-scale="month">월간</button>
    <span style="width:1px;height:20px;background:#d1d5db;margin:0 4px;"></span>
    <button data-range="1" class="active">1개월</button>
    <button data-range="3">3개월</button>
    <button data-range="6">6개월</button>
    <button data-range="12">1년</button>
    <span style="width:1px;height:20px;background:#d1d5db;margin:0 4px;"></span>
    <button onclick="collapseAll()">전체 접기</button>
    <button onclick="expandAll()">전체 펼치기</button>
    <span style="width:1px;height:20px;background:#d1d5db;margin:0 4px;"></span>
    <button id="toggleDone" onclick="toggleDone()">완료 숨기기</button>
  </div>
</div>

<div id="gantt_here"></div>

<div class="day-note-pop" id="dayNotePop">
  <h4 id="dnDate"></h4>
  <div class="dn-text" id="dnView"></div>
</div>

<div class="day-note-tip" id="dayNoteTip">
  <div class="dn-tip-title" id="dnTipTitle"></div>
  <div class="dn-tip-text" id="dnTipText"></div>
</div>

<script>
// 한글 로케일
gantt.locale = {
  date: {
    month_full: ["1월","2월","3월","4월","5월","6월","7월","8월","9월","10월","11월","12월"],
    month_short: ["1월","2월","3월","4월","5월","6월","7월","8월","9월","10월","11월","12월"],
    day_full: ["일요일","월요일","화요일","수요일","목요일","금요일","토요일"],
    day_short: ["일","월","화","수","목","금","토"]
  },
  labels: {
    new_task: "새 업무", icon_save: "저장", icon_cancel: "취소", icon_details: "상세",
    icon_edit: "편집", icon_delete: "삭제", confirm_closing: "",
    confirm_deleting: "정말 삭제하시겠습니까?",
    section_description: "설명", section_time: "기간",
    column_wbs: "WBS", column_text: "업무명",
    column_start_date: "시작일", column_duration: "기간", column_add: "",
    type_task: "업무", type_project: "프로젝트", type_milestone: "마일스톤",
    minutes: "분", hours: "시간", days: "일", weeks: "주", months: "월", years: "년"
  }
};

try { gantt.plugins({ tooltip: true }); } catch(e) {}

gantt.config.date_format = "%d-%m-%Y %H:%i";
gantt.config.open_tree_initially = false;  // open 값은 빌드 시 지정 (완료 가지는 접힘)
gantt.config.show_progress = true;
gantt.config.row_height = 38;
gantt.config.bar_height = 24;
gantt.config.scale_height = 64;
gantt.config.min_column_width = 56;
gantt.config.readonly = true;
gantt.config.autofit = false;

// 상태 자동 판별 함수
function getTaskStatus(task) {
  if (task.is_section || task.type === gantt.config.types.project) return '';
  if (task.custom_status === 'undetermined') return 'undetermined';
  if (task.done) return 'completed';
  var today = new Date(); today.setHours(0,0,0,0);
  var start = task.start_date ? new Date(task.start_date) : null;
  var end = task.end_date ? new Date(task.end_date) : null;
  if (start) start.setHours(0,0,0,0);
  if (end) end.setHours(0,0,0,0);
  if (start && end) {
    // end는 배타적(마지막날+1). 마일스톤은 end==start라 하루로 보정
    if (end.getTime() <= start.getTime()) { end = new Date(start); end.setDate(end.getDate() + 1); }
    if (today < start) return 'upcoming';
    if (today < end) return 'in-progress';
    return 'completed';
  }
  return '';
}
function escHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// 주말 제외 근무일 수 (시작일은 항상 1일째로 포함, end는 배타적)
function workDaysBetween(start, end) {
  var n = 1;
  var d = new Date(start.getTime());
  d.setDate(d.getDate() + 1);
  while (d < end) {
    var wd = d.getDay();
    if (wd !== 0 && wd !== 6) n++;
    d.setDate(d.getDate() + 1);
  }
  return n;
}

// 컬럼
gantt.config.columns = [
  { name: "text", label: "업무명", tree: true, width: 300, resize: true },
  { name: "status", label: "상태", align: "center", width: 60,
    template: function(task) {
      if (task.is_recur) return '<span class="status-badge status-recur">반복</span>';
      var st = getTaskStatus(task);
      if (st === 'in-progress') return '<span class="status-badge status-in-progress">진행중</span>';
      if (st === 'upcoming') return '<span class="status-badge status-upcoming">예정</span>';
      if (st === 'undetermined') return '<span class="status-badge status-undetermined">미정</span>';
      if (st === 'completed') return '<span class="status-badge" style="background:#e8efe9;color:#6b7280;">완료</span>';
      return '';
    }
  },
  { name: "start_date", label: "시작", align: "center", width: 100,
    template: function(task) {
      if (task.is_section || task.is_past_group) return "";
      if (task.is_recur) {
        return '<span style="font-size:11px;color:#6b7280">' + escHtml(task.recur_text || "") + '</span>';
      }
      return gantt.templates.date_grid(task.start_date, task);
    }
  },
  { name: "duration", label: "기간(일)", align: "center", width: 90,
    template: function(task) {
      if (task.is_section || task.is_past_group) return "";
      if (task.is_recur) {
        if (task.recur_open) return '<span style="font-size:11px;color:#9ca3af">무기한</span>';
        return (task.occurrences ? task.occurrences.length : 0) + "회";
      }
      if (task.is_single_event && task.start_date && task.end_date) {
        var d = Math.round((task.end_date - task.start_date) / 86400000);
        return d || 1;  // 일회성 일정은 주말 포함 달력일
      }
      if (task.work_days !== undefined) return task.work_days;
      if (task.start_date && task.end_date) return workDaysBetween(task.start_date, task.end_date);
      return task.duration;
    }
  },
  { name: "notes", label: "메모", width: 160, resize: true,
    template: function(task) {
      if (!task.notes) return '';
      var short = task.notes.replace(/\n/g, ' ');
      if (short.length > 25) short = short.substring(0, 25) + '...';
      return '<span style="font-size:11px;color:#6b7280" title="' + escHtml(task.notes).replace(/\n/g, '&#10;') + '">' + escHtml(short) + '</span>';
    }
  }
];

// 스케일
gantt.config.scales = [
  { unit: "month", step: 1, format: "%Y년 %M" },
  { unit: "day", step: 1, format: dayScaleFormat }
];

var scaleConfigs = {
  day: {
    scale_height: 64, min_column_width: 56,
    scales: [
      { unit: "month", step: 1, format: "%Y년 %M" },
      { unit: "day", step: 1, format: dayScaleFormat }
    ]
  },
  week: {
    scale_height: 56, min_column_width: 80,
    scales: [
      { unit: "month", step: 1, format: "%Y년 %M" },
      { unit: "week", step: 1, format: "%W주차" }
    ]
  },
  month: {
    scale_height: 56, min_column_width: 60,
    scales: [
      { unit: "year", step: 1, format: "%Y년" },
      { unit: "month", step: 1, format: "%M" }
    ]
  }
};

var currentScale = 'day';
function setScale(level) {
  var cfg = scaleConfigs[level];
  gantt.config.scale_height = cfg.scale_height;
  gantt.config.min_column_width = cfg.min_column_width;
  gantt.config.scales = cfg.scales;
  currentScale = level;
  if (document.body) document.body.classList.toggle("scale-day", level === "day");
  gantt.render();
}

// 주말 + 공휴일
var HOLIDAYS = {{HOLIDAYS}};  // {날짜: 이름}
function holidayName(date) {
  var k = date.getFullYear() + "-" + ("0" + (date.getMonth() + 1)).slice(-2) + "-" + ("0" + date.getDate()).slice(-2);
  return HOLIDAYS[k] || "";
}
function isHoliday(date) { return !!holidayName(date); }

// 날짜 메모 (보기 전용) — 눈금에는 표식만, 내용은 클릭해야 보인다
var DAY_NOTES = {{DAY_NOTES}};  // {날짜: 메모}
function isoOf(date) {
  return date.getFullYear() + "-" + ("0" + (date.getMonth() + 1)).slice(-2) +
         "-" + ("0" + date.getDate()).slice(-2);
}
function noteMark(date) {
  return DAY_NOTES[isoOf(date)] ? "<span class='day-note-mark'>📝</span>" : "";
}

// 클릭/hover 지점이 가리키는 메모 찾기 (있을 때만 반환).
// 눈금 칸 = 날짜 전체 메모, 격자 칸·막대 = 그 행 항목의 그 날짜 메모.
function dnFound(iso, tid) {
  var key = null, label = iso;
  var d = isoToDate(iso);
  if (d) label += " (" + gantt.locale.date.day_short[d.getDay()] + ")";
  if (tid && DAY_NOTES[iso + "|" + tid]) {
    key = iso + "|" + tid;
    if (gantt.isTaskExists(tid)) label = gantt.getTask(tid).text + " · " + label;
  } else if (!tid && DAY_NOTES[iso]) {
    key = iso;
  }
  return key ? { key: key, label: label } : null;
}
function dnResolve(e) {
  if (currentScale !== "day") return null;
  var bar = e.target.closest && e.target.closest(".gantt_task_line");
  if (bar) {  // 막대가 격자 칸을 가리므로 좌표로 날짜를 계산
    var area = document.querySelector(".gantt_bars_area");
    if (!area) return null;
    var d = gantt.dateFromPos(e.clientX - area.getBoundingClientRect().left);
    return d ? dnFound(isoOf(d), bar.getAttribute("task_id")) : null;
  }
  var cell = e.target.closest && e.target.closest(".gantt_scale_cell, .gantt_task_cell");
  if (!cell) return null;
  var d2 = gantt.dateFromPos(cell.offsetLeft + 2);
  if (!d2) return null;
  var row = cell.closest(".gantt_task_row");
  return dnFound(isoOf(d2), row ? row.getAttribute("task_id") : null);
}

function dayScaleFormat(date) {
  var h = holidayName(date);
  if (h) return "<div class='hd'><span class='hd-d'>" + date.getDate() + "</span><span class='hd-n'>" + h + "</span></div>" + noteMark(date);
  return date.getDate() + "(" + gantt.locale.date.day_short[date.getDay()] + ")" + noteMark(date);
}

// 표식·메모 칸·막대 클릭 = 메모 내용 보기 (정적 뷰어는 편집 없음)
function dnShowPop(info, e) {
  var pop = document.getElementById("dayNotePop");
  var tip = document.getElementById("dayNoteTip");
  if (tip) tip.classList.remove("show");
  document.getElementById("dnDate").textContent = info.label;
  document.getElementById("dnView").textContent = DAY_NOTES[info.key];
  pop.classList.add("show");
  pop.style.left = Math.max(8, Math.min(e.clientX - 20, window.innerWidth - pop.offsetWidth - 12)) + "px";
  pop.style.top  = Math.min(e.clientY + 8, window.innerHeight - pop.offsetHeight - 12) + "px";
}
document.addEventListener("click", function(e) {
  var pop = document.getElementById("dayNotePop");
  if (!pop || pop.contains(e.target)) return;
  var info = dnResolve(e);
  if (!info) { pop.classList.remove("show"); return; }
  dnShowPop(info, e);
}, false);
// dhtmlx가 막대 클릭의 전파를 삼키는 경우 대비 — 공식 이벤트로도 같은 동작
gantt.attachEvent("onTaskClick", function(id, e) {
  if (e && e.target && e.target.closest && e.target.closest(".gantt_task_line")) {
    var info = dnResolve(e);
    if (info) dnShowPop(info, e);
  }
  return true;
});

// 메모가 있는 칸·막대 위에 마우스를 올리면 클릭 없이 내용 툴팁 표시
// (막대 위에서는 같은 요소 안에서 날짜가 바뀌므로 mousemove로 추적)
var dnTipKey = null;
document.addEventListener("mousemove", function(e) {
  var tip = document.getElementById("dayNoteTip");
  if (!tip) return;
  var pop = document.getElementById("dayNotePop");
  var info = dnResolve(e);
  if (!info || (pop && pop.classList.contains("show"))) {
    if (dnTipKey !== null) { tip.classList.remove("show"); dnTipKey = null; }
    return;
  }
  if (info.key === dnTipKey) return;
  dnTipKey = info.key;
  document.getElementById("dnTipTitle").textContent = info.label;
  document.getElementById("dnTipText").textContent = DAY_NOTES[info.key];
  tip.classList.add("show");
  tip.style.left = Math.max(8, Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 12)) + "px";
  tip.style.top  = Math.min(e.clientY + 18, window.innerHeight - tip.offsetHeight - 12) + "px";
}, false);
gantt.templates.scale_cell_class = function(date) {
  if (isHoliday(date)) return "holiday";
  if (date.getDay() === 0) return "weekend sunday";
  if (date.getDay() === 6) return "weekend saturday";
  return "";
};
gantt.templates.timeline_cell_class = function(item, date) {
  var cls = "";
  if (isHoliday(date)) cls = "holiday";
  else if (date.getDay() === 0 || date.getDay() === 6) cls = "weekend";
  if (item.band_start) {
    var ymd = date.getFullYear() * 10000 + (date.getMonth() + 1) * 100 + date.getDate();
    if (ymd >= item.band_start && ymd <= item.band_end) {
      cls += " range-band band-" + item.band_theme;
    }
  }
  var t = new Date();
  if (date.getFullYear() === t.getFullYear() && date.getMonth() === t.getMonth() && date.getDate() === t.getDate()) {
    cls += " today";
  }
  if (currentScale === "day" && DAY_NOTES[isoOf(date) + "|" + item.id]) cls += " day-note-cell";
  return cls;
};

// 행 스타일
gantt.templates.grid_row_class = function(start, end, task) {
  var cls = [];
  if (task.is_section === 'project') cls.push("section-row");
  else if (task.is_section === 'event') cls.push("section-row-event");
  else if (task.is_section === 'recurring') cls.push("section-row-recur");
  else if (task.is_recur) cls.push("recur-row");
  else if (task.is_parent_project) cls.push("project-row");
  if (task.is_past_group) cls.push("past-group-row");
  if (task.is_sub_project) cls.push("sub-project-row");
  if (task.is_single_event) cls.push("single-event-row");
  if (task.custom_status === 'undetermined') cls.push("undetermined-row");
  if (getTaskStatus(task) === 'completed') {
    cls.push("done-row");
    if (task.bar_level === 3) cls.push("leaf-row");  // 취소선은 잎에만
  }
  return cls.join(" ");
};
gantt.templates.task_row_class = function(start, end, task) {
  var cls = [];
  if (task.is_section === 'project') cls.push("section-row");
  else if (task.is_section === 'event') cls.push("section-row-event");
  else if (task.is_section === 'recurring') cls.push("section-row-recur");
  if (task.is_single_event) cls.push("single-event-row");
  if (task.custom_status === 'undetermined') cls.push("undetermined-row");
  return cls.join(" ");
};

// 반복 일정: 회차마다 다이아몬드를 타임라인에 직접 그린다
// (이 dhtmlx 판에는 addTaskLayer API가 없어 오늘 선과 같은 방식 사용)
function renderRecurDots() {
  var area = document.querySelector(".gantt_bars_area") || document.querySelector(".gantt_task");
  if (!area) return;
  Array.prototype.forEach.call(area.querySelectorAll(".recur-dot"), function(el) { el.remove(); });
  gantt.eachTask(function(task) {
    if (!task.is_recur || !task.occurrences || !task.occurrences.length) return;
    if (!gantt.isTaskVisible(task.id)) return;
    var top = gantt.getTaskTop(task.id) + (gantt.config.row_height - 11) / 2;
    task.occurrences.forEach(function(iso) {
      var p = iso.split("-");
      var d = new Date(+p[0], +p[1] - 1, +p[2], 12, 0, 0);
      var x = gantt.posFromDate(d);
      if (x < -20) return;
      var dot = document.createElement("div");
      dot.className = "recur-dot " + (task.color_class || "");
      dot.style.left = (x - 5.5) + "px";
      dot.style.top = top + "px";
      area.appendChild(dot);
    });
  });
}

// 바 위 텍스트 숨기기
gantt.templates.task_text = function(start, end, task) { return ""; };

// 바 오른쪽 텍스트: 마일스톤만 일정명 표시
gantt.templates.rightside_text = function(start, end, task) {
  // 일회성 일정은 하루짜리(다이아몬드)든 여러 날(기간 바)이든 옆에 이름 표시
  if (task.type === "milestone" || task.is_single_event) {
    return escHtml(task.text) + (task.custom_time ? " · " + escHtml(task.custom_time) : "");
  }
  return "";
};

// 바 색상 + 레벨 위계 + 완료 처리
gantt.templates.task_class = function(start, end, task) {
  if (task.is_section || task.is_past_group || task.is_recur) return "hide-bar";
  var cls = task.color_class || "";
  if (task.bar_level === 1) cls += " lv-project";
  else if (task.bar_level === 2) cls += " lv-subproject";
  else if (task.type !== "milestone") cls += " lv-task";
  if (task.custom_status === 'undetermined') cls += " hide-bar";
  if (getTaskStatus(task) === 'completed') cls += " is-done";
  return cls;
};

// 툴팁
try {
  gantt.templates.tooltip_date_format = gantt.date.date_to_str("%Y-%m-%d");
  gantt.templates.tooltip_text = function(start, end, task) {
    if (task.is_section || task.is_past_group) return "";
    var h = "<b>" + escHtml(task.text) + "</b><br>";
    if (task.is_recur) {
      h += escHtml(task.recur_text || "반복");
      h += task.recur_open ? "<br>종료일 없음 (무기한 반복)"
                           : "<br>총 " + (task.occurrences ? task.occurrences.length : 0) + "회";
      if (task.custom_time) h += "<br>시간: " + escHtml(task.custom_time);
      if (task.notes) h += "<br><br><b>메모:</b><br>" + escHtml(task.notes).replace(/\n/g, "<br>");
      return h;
    }
    if (task.custom_status === 'undetermined') {
      h += "기간: 미정";
    } else {
      h += "시작: " + gantt.templates.tooltip_date_format(start) + "<br>";
      h += "종료: " + gantt.templates.tooltip_date_format(end);
    }
    if (task.custom_time) h += "<br>시간: " + escHtml(task.custom_time);
    if (task.notes) h += "<br><br><b>메모:</b><br>" + escHtml(task.notes).replace(/\n/g, "<br>");
    return h;
  };
  // 작업 정보 툴팁은 막대가 아니라 왼쪽 목록(그리드)에서만 띄운다
  // (막대 위 hover는 날짜 메모 툴팁 전용)
  gantt.attachEvent("onGanttReady", function() {
    gantt.ext.tooltips.detach("[" + gantt.config.task_attribute + "]:not(.gantt_task_row)");
    gantt.ext.tooltips.tooltipFor({
      selector: ".gantt_grid [" + gantt.config.task_attribute + "]",
      html: function(event, node) {
        var id = node.getAttribute(gantt.config.task_attribute);
        if (!id || !gantt.isTaskExists(id)) return null;
        var t = gantt.getTask(id);
        return gantt.templates.tooltip_text(t.start_date, t.end_date, t) || null;
      }
    });
  });
} catch(e) {}

// 초기화
gantt.init("gantt_here");

// 오늘 표시선 + 반복 일정 다이아몬드
gantt.attachEvent("onGanttRender", function() {
  var today = new Date();
  var areaEl = document.querySelector(".gantt_task");
  if (!areaEl) return;
  var old = document.getElementById("today_line");
  if (old) old.remove();
  var pos = gantt.posFromDate(today);
  if (pos > 0) {
    var line = document.createElement("div");
    line.id = "today_line";
    line.style.cssText = "position:absolute;top:0;left:"+pos+"px;width:2px;height:100%;background:#4f46e5;opacity:0.5;z-index:5;pointer-events:none;";
    areaEl.appendChild(line);
  }
  try { renderRecurDots(); } catch(e) {}
});

// ============ 데이터 (빌드 시 자동 생성) ============
gantt.parse({
  data: {{GANTT_DATA}},
  links: []
});

// 완료 숨기기 토글: 완료된 항목은 종류를 가리지 않고 숨긴다
// (진행 중 프로젝트 아래의 완료된 개별 업무 포함 — 사용자 피드백 반영)
var hideDone = localStorage.getItem('hideDone') === '1';
function updateDoneBtn() {
  var b = document.getElementById('toggleDone');
  if (b) b.classList.toggle('active', hideDone);
}
function toggleDone() {
  hideDone = !hideDone;
  localStorage.setItem('hideDone', hideDone ? '1' : '0');
  updateDoneBtn();
  gantt.render();
}
updateDoneBtn();
gantt.attachEvent("onBeforeTaskDisplay", function(id, task) {
  if (!hideDone) return true;
  if (task.is_past_group) return false;
  if (!task.is_section && !task.is_recur && getTaskStatus(task) === 'completed') return false;
  return true;
});

// 범위 조절 (스케일 자동 전환 + 자유 스크롤)
var rangeScaleMap = { 1: 'day', 3: 'week', 6: 'week', 12: 'month' };
function setRange(months) {
  // 스케일 자동 전환
  var autoScale = rangeScaleMap[months] || 'day';
  var cfg = scaleConfigs[autoScale];
  gantt.config.scale_height = cfg.scale_height;
  gantt.config.min_column_width = cfg.min_column_width;
  gantt.config.scales = cfg.scales;
  currentScale = autoScale;
  if (document.body) document.body.classList.toggle("scale-day", autoScale === "day");
  // 날짜 제한 해제 → 자유 스크롤
  gantt.config.start_date = null;
  gantt.config.end_date = null;
  // 스케일 버튼 active 동기화
  document.querySelectorAll('[data-scale]').forEach(function(b) { b.classList.remove('active'); });
  var activeBtn = document.querySelector('[data-scale="' + autoScale + '"]');
  if (activeBtn) activeBtn.classList.add('active');
  gantt.render();
  // 오늘로 스크롤
  gantt.showDate(new Date());
}
setRange(1);

// 스케일 전환
document.querySelectorAll('[data-scale]').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('[data-scale]').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    gantt.config.start_date = null;
    gantt.config.end_date = null;
    setScale(btn.dataset.scale);
    gantt.showDate(new Date());
  });
});

// 범위 전환
document.querySelectorAll('[data-range]').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('[data-range]').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    setRange(parseInt(btn.dataset.range));
  });
});

function collapseAll() {
  gantt.eachTask(function(t) { t.$open = false; });
  gantt.render();
}
function expandAll() {
  gantt.eachTask(function(t) { t.$open = true; });
  gantt.render();
}
</script>
</body>
</html>'''


def main():
    ensure_data_file()
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    gantt_items = build_gantt_data(data)
    # "</script>" 포함 텍스트가 페이지를 깨뜨리지 않도록 이스케이프
    gantt_json = json.dumps(gantt_items, ensure_ascii=False, indent=4).replace("</", "<\\/")
    color_css = build_color_css(data.get("colors", {}))
    last_updated = data.get("last_updated", datetime.now().strftime("%Y-%m-%d"))

    html = HTML_TEMPLATE
    html = html.replace("{{GANTT_DATA}}", gantt_json)
    html = html.replace("{{COLOR_CSS}}", color_css)
    html = html.replace("{{LAST_UPDATED}}", last_updated)
    html = html.replace("{{HOLIDAYS}}", json.dumps(load_holidays(), ensure_ascii=False))
    html = html.replace("{{DAY_NOTES}}",
                        json.dumps(day_note_map(data), ensure_ascii=False).replace("</", "<\\/"))

    # 원자적 쓰기: 재빌드 도중 프로세스가 종료돼도 공유용 HTML이 절반만 남지 않도록
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    tmp = OUTPUT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp, OUTPUT_FILE)

    print(f"빌드 완료: {OUTPUT_FILE}")
    print(f"  - 섹션: {len(data['sections'])}개")
    print(f"  - Gantt 항목: {len(gantt_items)}개")
    print(f"  - 업데이트: {last_updated}")

    next_year = datetime.now().year + 1
    if not any(k.startswith(str(next_year)) for k in load_holidays()):
        print(f"  ! 공휴일 데이터에 {next_year}년이 아직 없습니다 (앱 실행 시 자동 갱신됩니다)")


if __name__ == "__main__":
    main()
