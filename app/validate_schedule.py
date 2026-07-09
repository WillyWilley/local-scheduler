# -*- coding: utf-8 -*-
"""
schedule_data.json 검증기
- 앱 저장 시 자동 실행 (실패하면 저장 거부)
- 단독 실행: python validate_schedule.py  → 현재 파일 검사
"""
import json
import os
import re
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))   # app/
BASE_DIR = os.path.dirname(SCRIPT_DIR)                     # 프로젝트 루트
DATA_FILE = os.path.join(BASE_DIR, "data", "schedule_data.json")

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RESERVED_ID_RE = re.compile(r"^sec-|-past$")  # 섹션·지난일정 그룹이 쓰는 ID 공간


def _check_date(value, where, errors):
    # 패딩까지 강제 (비패딩 날짜는 문자열 정렬을 깨뜨림)
    if not DATE_RE.match(str(value)):
        errors.append(f"{where}: 날짜 형식 오류 ({value!r}, YYYY-MM-DD 필요)")
        return
    try:
        datetime.strptime(str(value), "%Y-%m-%d")
    except ValueError:
        errors.append(f"{where}: 존재하지 않는 날짜 ({value!r})")


def _check_leaf(obj, where, errors, require_dates):
    has_start = bool(obj.get("start"))
    has_dur = obj.get("duration") is not None
    if has_start != has_dur:
        errors.append(f"{where}: start와 duration은 함께 있어야 함")
    if has_start:
        _check_date(obj["start"], where, errors)
    if has_dur:
        dur = obj["duration"]
        if isinstance(dur, bool) or not isinstance(dur, int):
            errors.append(f"{where}: duration은 정수여야 함 ({dur!r})")
        elif dur < 1:
            errors.append(f"{where}: duration은 1 이상이어야 함")
    if require_dates and not has_start:
        errors.append(f"{where}: 날짜가 필요함")
    prog = obj.get("progress")
    if prog is not None:
        try:
            if not (0 <= float(prog) <= 1):
                errors.append(f"{where}: progress는 0~1 범위여야 함 ({prog!r})")
        except (ValueError, TypeError):
            errors.append(f"{where}: progress가 숫자가 아님 ({prog!r})")
    status = obj.get("status")
    if status is not None and status != "undetermined":
        errors.append(f"{where}: status는 'undetermined'만 허용 ({status!r})")


def validate(data):
    """오류 메시지 리스트 반환 (비어 있으면 통과)"""
    errors = []

    if not isinstance(data, dict):
        return ["최상위가 객체가 아님"]
    if "sections" not in data or not isinstance(data["sections"], list):
        return ["sections 배열이 없음"]

    colors = set(data.get("colors", {}).keys())
    seen_ids = set()

    def check_id(obj, where, allow_reserved=False):
        oid = obj.get("id")
        if oid is None:
            errors.append(f"{where}: id 없음")
            return
        if str(oid) in seen_ids:
            errors.append(f"{where}: id 중복 ({oid})")
        if not allow_reserved and RESERVED_ID_RE.search(str(oid)):
            errors.append(f"{where}: 예약된 ID 형식 ({oid!r} — 'sec-*'/'*-past'는 시스템용)")
        seen_ids.add(str(oid))

    def check_list(parent, key, where):
        v = parent.get(key, [])
        if not isinstance(v, list):
            errors.append(f"{where}: {key}는 배열이어야 함 ({type(v).__name__})")
            return []
        out = []
        for x in v:
            if isinstance(x, dict):
                out.append(x)
            else:
                errors.append(f"{where}: {key} 안에 객체가 아닌 항목이 있음 ({x!r})")
        return out

    def check_color(obj, where):
        c = obj.get("color", "")
        if c and c not in colors:
            errors.append(f"{where}: 정의되지 않은 색상 '{c}'")

    for si, sec in enumerate(data["sections"]):
        sw = f"섹션[{si}]"
        if not isinstance(sec, dict):
            errors.append(f"{sw}: 객체가 아님")
            continue
        check_id(sec, sw, allow_reserved=True)
        if sec.get("type") not in ("project", "event"):
            errors.append(f"{sw}: type은 project/event 중 하나여야 함")
        if not sec.get("title"):
            errors.append(f"{sw}: title 없음")

        if sec.get("type") == "project":
            for pi, proj in enumerate(check_list(sec, "projects", sw)):
                pw = f"{sw}.프로젝트[{pi}]({proj.get('title', '?')})"
                check_id(proj, pw)
                check_color(proj, pw)
                if not proj.get("title"):
                    errors.append(f"{pw}: title 없음")
                _check_leaf(proj, pw, errors, require_dates=False)
                for xi, sub in enumerate(check_list(proj, "sub_projects", pw)):
                    xw = f"{pw}.세부[{xi}]({sub.get('title', '?')})"
                    check_id(sub, xw)
                    if not sub.get("title"):
                        errors.append(f"{xw}: title 없음")
                    _check_leaf(sub, xw, errors, require_dates=False)
                    for ti, task in enumerate(check_list(sub, "tasks", xw)):
                        tw = f"{xw}.업무[{ti}]({task.get('title', '?')})"
                        check_id(task, tw)
                        if not task.get("title"):
                            errors.append(f"{tw}: title 없음")
                        _check_leaf(task, tw, errors, require_dates=False)
        else:
            for ei, evt in enumerate(check_list(sec, "events", sw)):
                ew = f"{sw}.일정[{ei}]({evt.get('title', '?')})"
                check_id(evt, ew)
                check_color(evt, ew)
                if not evt.get("title"):
                    errors.append(f"{ew}: title 없음")
                _check_leaf(evt, ew, errors, require_dates=True)

    return errors


def main():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    errors = validate(data)
    if errors:
        print(f"검증 실패 ({len(errors)}건):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print("검증 통과")


if __name__ == "__main__":
    main()
