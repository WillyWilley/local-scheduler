# -*- coding: utf-8 -*-
"""
업무 스케줄 앱 (뷰어 + 에디터 통합)

업무스케줄.bat 더블클릭 → 독립 앱 창이 열림.
- 평소엔 완성된 스케줄표 그대로 보기
- 바 드래그·더블클릭·행 추가로 그 자리에서 바로 편집
- 저장 시 schedule_data.json 갱신 + 업무_스케줄.html(NAS 공유용) 자동 재빌드
- 서버는 백그라운드 상주 (창을 닫아도 유지 → 다음 실행이 즉시 열림,
  코드가 수정되면 버전 표식으로 감지해 자동 교체)

직접 실행: python schedule_web_editor.py
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))   # app/
_BASE_DIR = os.path.dirname(_SCRIPT_DIR)                    # 프로젝트 루트


def _setup_logging():
    """pythonw(콘솔 없음)로 실행될 때 출력을 로그 파일로 돌린다.
    (여러 PC가 같은 폴더를 공유해도 충돌하지 않도록 호스트명 포함)"""
    if sys.stdout is None or sys.stderr is None:
        host = os.environ.get("COMPUTERNAME", "pc")
        log_dir = os.path.join(_BASE_DIR, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"app-{host}.log")
        try:  # 로그가 무한히 커지지 않도록 1MB 넘으면 새로 시작
            if os.path.getsize(log_path) > 1_000_000:
                os.remove(log_path)
        except OSError:
            pass
        log = open(log_path, "a", encoding="utf-8", buffering=1)
        sys.stdout = log
        sys.stderr = log
        print(f"\n===== 앱 시작 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====")


_setup_logging()  # 이후의 import 오류까지 로그에 남도록 최대한 일찍 실행

import build_schedule as bs
import validate_schedule

PORT = 8765
# 콘솔 자식 프로세스(netstat 등)가 pythonw 아래에서 검은 창을 띄우지 않도록
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
BACKUP_DIR = os.path.join(bs.BASE_DIR, "data", "backups")
BACKUP_KEEP = 30


# ══════════════════════ 앱 화면용 데이터 ══════════════════════
# 보기용 빌드 결과(색상·위계·완료정렬·지난일정 그룹)를 그대로 쓰되,
# 편집에 필요한 메타데이터(kind, color_key)를 덧붙인다.

def build_app_items(data):
    items = bs.build_gantt_data(data)

    kind_map, color_map = {}, {}
    for sec in data["sections"]:
        for p in sec.get("projects", []):
            kind_map[str(p["id"])] = "project"
            color_map[str(p["id"])] = p.get("color", "")
            for s in p.get("sub_projects", []):
                kind_map[str(s["id"])] = "sub"
                for t in s.get("tasks", []):
                    kind_map[str(t["id"])] = "task"
        for e in sec.get("events", []):
            kind_map[str(e["id"])] = "event"
            color_map[str(e["id"])] = e.get("color", "")

    for it in items:
        sid = str(it["id"])
        if sid in kind_map:
            it["kind"] = kind_map[sid]
        if sid in color_map:
            it["color_key"] = color_map[sid]
        # 편집 후에도 기간(근무일)이 실시간 재계산되도록 정적 값 제거
        it.pop("work_days", None)
        # 롤업 부모는 gantt가 자식 범위를 실시간 계산하도록 project 타입 부여
        if it.get("bar_level") in (1, 2) and not it.get("is_section"):
            it["type"] = "project"
        # 인라인 편집기가 undefined를 표시하지 않도록 빈 메모 기본값
        if not it.get("is_section") and not it.get("is_past_group"):
            it.setdefault("notes", "")
    return items


# ══════════════════════ 저장: 간트 트리 → 원본 JSON ══════════════════════

def _parse_gantt_date(s):
    s = str(s).strip()
    for fmt in ("%d-%m-%Y %H:%M", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(f"날짜 형식을 해석할 수 없음: {s}")


def normalize_flat(flat):
    """'지난 일정' 그룹 노드를 제거하고 그 자식들을 원래 섹션 소속으로 되돌린다."""
    groups = {str(n["id"]): n for n in flat if n.get("is_past_group")}
    out = []
    for n in flat:
        if n.get("is_past_group"):
            continue
        p = str(n.get("parent", ""))
        if p in groups:
            n = dict(n)
            n["parent"] = groups[p].get("parent")
        out.append(n)
    return out


def save_from_flat(flat, data):
    """gantt.serialize() 결과(플랫 트리)를 schedule_data.json 구조로 역변환.
    기존 항목은 원본 dict를 재사용해 알 수 없는 필드를 보존한다."""
    today_iso = datetime.now().strftime("%Y-%m-%d")
    flat = normalize_flat(flat)

    children = {}
    for n in flat:
        children.setdefault(str(n.get("parent", "")), []).append(n)

    orig = {}
    for sec in data["sections"]:
        for p in sec.get("projects", []):
            orig[str(p["id"])] = p
            for s in p.get("sub_projects", []):
                orig[str(s["id"])] = s
                for t in s.get("tasks", []):
                    orig[str(t["id"])] = t
        for e in sec.get("events", []):
            orig[str(e["id"])] = e

    numeric_ids = [int(k) for k in orig if k.lstrip("-").isdigit()]
    next_id = (max(numeric_ids) + 1) if numeric_ids else 1

    def get_or_new(node):
        nonlocal next_id
        o = orig.get(str(node["id"]))
        if o is None:  # 새로 추가된 항목 → 간결한 ID로 재부여
            o = {"id": next_id}
            next_id += 1
        return o

    def leaf_dates(node):
        """(시작 dt, 마지막날 dt) 또는 None(미정)"""
        if node.get("unscheduled") or not node.get("start_date"):
            return None
        s = _parse_gantt_date(node["start_date"]).replace(hour=0, minute=0, second=0, microsecond=0)
        if node.get("end_date"):
            e = _parse_gantt_date(node["end_date"]).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        else:
            e = s  # 마일스톤 등 end 없는 항목은 하루짜리
        if e < s:
            e = s
        return s, e

    def leaf_schedule(node):
        """(시작 ISO, 근무일수) 또는 None(미정) — 장기 업무용"""
        d = leaf_dates(node)
        if not d:
            return None
        s, e = d
        return s.strftime("%Y-%m-%d"), bs.workdays_between(s, e)

    def upd_common(node, obj):
        title = (node.get("text") or "").strip()
        if title:
            obj["title"] = title
        obj.setdefault("title", "새 항목")
        notes = (node.get("notes") or "").strip()
        if notes:
            obj["notes"] = notes
        else:
            obj.pop("notes", None)
        if node.get("done"):
            obj["done"] = True
        else:
            obj.pop("done", None)

    def upd_color(node, obj):
        ck = (node.get("color_key") or "").strip()
        if ck:
            obj["color"] = ck
        else:
            obj.pop("color", None)

    def upd_leaf_dates(node, obj):
        sch = leaf_schedule(node)
        if sch:
            obj["start"], obj["duration"] = sch
            # 미정 지정/해제는 클라이언트의 custom_status를 그대로 따른다
            # (미정 + 예상 날짜 공존 가능; 편집창의 '날짜 미정' 체크로 토글)
            if node.get("custom_status") == "undetermined":
                obj["status"] = "undetermined"
            else:
                obj.pop("status", None)
        else:
            obj.pop("start", None)
            obj.pop("duration", None)
            if node.get("custom_status") == "undetermined":
                obj["status"] = "undetermined"

    def map_task(node):
        obj = get_or_new(node)
        upd_common(node, obj)
        upd_leaf_dates(node, obj)
        obj.setdefault("progress", 0)
        obj.pop("tasks", None)
        obj.pop("sub_projects", None)
        return obj

    def map_sub(node):
        obj = get_or_new(node)
        upd_common(node, obj)
        kids = children.get(str(node["id"]), [])
        obj["tasks"] = [map_task(k) for k in kids]
        obj.pop("sub_projects", None)
        if kids:
            # 자식이 있으면 일정·완료는 롤업으로 계산되므로 직접 값 제거
            obj.pop("start", None)
            obj.pop("duration", None)
            obj.pop("progress", None)
            obj.pop("done", None)
        else:
            upd_leaf_dates(node, obj)
            obj.setdefault("progress", 0)
        return obj

    def map_project(node):
        obj = get_or_new(node)
        upd_common(node, obj)
        upd_color(node, obj)
        kids = children.get(str(node["id"]), [])
        obj["sub_projects"] = [map_sub(k) for k in kids]
        obj.pop("tasks", None)
        if kids:
            obj.pop("start", None)
            obj.pop("duration", None)
            obj.pop("progress", None)
            obj.pop("done", None)
        else:
            upd_leaf_dates(node, obj)
        return obj

    def map_event(node):
        obj = get_or_new(node)
        upd_common(node, obj)
        upd_color(node, obj)
        d = leaf_dates(node)
        if d:
            s, e = d
            obj["start"] = s.strftime("%Y-%m-%d")
            obj["duration"] = (e - s).days + 1  # 일회성 일정은 주말 포함 달력일
        obj.setdefault("start", today_iso)
        obj.setdefault("duration", 1)
        t = (node.get("custom_time") or "").strip()
        if t:
            obj["time"] = t
        else:
            obj.pop("time", None)
        return obj

    for sec in data["sections"]:
        kids = children.get(str(sec["id"]), [])
        if sec["type"] == "project":
            sec["projects"] = [map_project(k) for k in kids]
        else:
            sec["events"] = [map_event(k) for k in kids]

    # 색상 자동 배정: 색을 고르지 않은('자동') 프로젝트/일정에
    # 가장 적게 쓰인 팔레트 색을 부여해 전체가 골고루 정돈되게 한다.
    palette = list(data.get("colors", {}).keys())
    if palette:
        usage = {k: 0 for k in palette}
        targets = []
        for sec in data["sections"]:
            for obj in sec.get("projects", []) + sec.get("events", []):
                c = obj.get("color")
                if c in usage:
                    usage[c] += 1
                elif not c:
                    targets.append(obj)
        for obj in targets:
            pick = min(palette, key=lambda k: (usage[k], palette.index(k)))
            obj["color"] = pick
            usage[pick] += 1

    data["last_updated"] = today_iso
    return data


def merge_new_colors(data, new_colors):
    """앱에서 추가한 새 색상을 팔레트에 병합 (키·색상값 검증)"""
    colors = data.setdefault("colors", {})
    for key, v in (new_colors or {}).items():
        key = str(key)
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,20}", key) or key in colors:
            continue
        bg = str(v.get("bg", ""))
        border = str(v.get("border", bg))
        if re.fullmatch(r"#[0-9a-fA-F]{6}", bg) and re.fullmatch(r"#[0-9a-fA-F]{6}", border):
            colors[key] = {"bg": bg, "border": border}


def backup_data():
    """저장 직전 스냅샷 (최근 BACKUP_KEEP개 유지)"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(bs.DATA_FILE, os.path.join(BACKUP_DIR, f"schedule_data_{stamp}.json"))
    old = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("schedule_data_"))
    for f in old[:-BACKUP_KEEP]:
        os.remove(os.path.join(BACKUP_DIR, f))


# ══════════════════════ 앱 페이지 (보기+편집 통합) ══════════════════════

APP_HTML = r'''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>업무 스케줄</title>
<script src="/assets/dhtmlxgantt.js?v={{APP_VERSION}}"></script>
<link href="/assets/dhtmlxgantt.css?v={{APP_VERSION}}" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700&display=swap"
      rel="stylesheet" media="print" onload="this.media='all'">
<style>
  html, body {
    margin: 0; padding: 0; height: 100%; overflow: hidden;
    font-family: 'Noto Sans KR', 'Malgun Gothic', sans-serif;
    background: #f5f7fa;
  }
  body { display: flex; flex-direction: column; }

  .page-header {
    background: #fff;
    border-bottom: 1px solid #e5e7eb;
    padding: 12px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    min-height: 64px;
    box-sizing: border-box;
    gap: 12px;
    flex-wrap: wrap;
    row-gap: 6px;
    flex-shrink: 0;
  }
  .page-header h1 { font-size: 19px; font-weight: 700; color: #1a1a2e; margin: 0; white-space: nowrap; }
  .page-header .subtitle { font-size: 11.5px; color: #9ca3af; margin-top: 2px; white-space: nowrap; }

  .header-controls { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; row-gap: 6px; }
  .btn-group {
    display: flex; background: #f3f4f6; border-radius: 8px; padding: 3px; gap: 2px;
  }
  .btn-group button {
    padding: 5px 13px; border: none; border-radius: 6px;
    background: transparent; color: #6b7280; font-family: inherit;
    font-size: 12px; font-weight: 500; cursor: pointer; transition: all 0.15s;
    white-space: nowrap;
  }
  .btn-group button:hover { color: #4f46e5; }
  .btn-group button.active { background: #fff; color: #4f46e5; font-weight: 600; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }

  .tool-btn {
    padding: 6px 14px; border: 1px solid #e5e7eb; border-radius: 8px;
    background: #fff; color: #374151; font-family: inherit;
    font-size: 12px; font-weight: 500; cursor: pointer; transition: all 0.15s;
    white-space: nowrap;
  }
  .tool-btn:hover { border-color: #4f46e5; color: #4f46e5; }
  .tool-btn.active { background: #4f46e5; color: #fff; border-color: #4f46e5; }

  #saveBtn {
    padding: 7px 22px; border: none; border-radius: 8px;
    background: #e5e7eb; color: #9ca3af; font-family: inherit;
    font-size: 13px; font-weight: 700; cursor: default; transition: all 0.2s;
    white-space: nowrap;
  }
  #saveBtn.dirty {
    background: #4f46e5; color: #fff; cursor: pointer;
    box-shadow: 0 2px 8px rgba(79,70,229,0.35);
  }
  #saveBtn.dirty:hover { background: #4338ca; }

  #toast {
    position: fixed; left: 50%; bottom: 28px; transform: translateX(-50%) translateY(80px);
    background: #1f2937; color: #fff; padding: 10px 24px; border-radius: 10px;
    font-size: 13px; font-weight: 500; z-index: 999; transition: transform 0.3s ease;
    box-shadow: 0 8px 24px rgba(0,0,0,0.25); pointer-events: none;
  }
  #toast.show { transform: translateX(-50%) translateY(0); }

  #gantt_here { width: 100%; flex: 1; min-height: 0; }

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

  .single-event-row .gantt_tree_content { padding-left: 10px !important; }

  /* 지난 일정 그룹 (접힌 보관함) */
  .past-group-row { background: #f9fafb !important; }
  .past-group-row .gantt_tree_content { color: #9ca3af !important; font-size: 12px !important; font-weight: 500; }

  .gantt_task_line.hide-bar { display: none !important; }
  .section-row .gantt_task_line { display: none !important; }

  .gantt_task_line { box-sizing: border-box !important; }
  .gantt_task_line.lv-project { border-radius: 2px; border-width: 0; background: #6b7280; }
  .gantt_task_line.lv-project .gantt_task_progress_wrapper,
  .gantt_task_line.lv-project .gantt_task_content { display: none; }
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

  /* ── 추가(+) 버튼: 추가 가능한 그룹 행에서 마우스를 올렸을 때만 표시 ── */
  .gantt_grid_head_add { background-image: none; pointer-events: none; }
  .gantt_grid_head_add::after {
    content: "추가"; font-size: 11px; color: #9ca3af; font-weight: 600;
  }
  .gantt_grid .gantt_add { opacity: 0; transition: opacity 0.15s; }
  .gantt_row:hover .gantt_add { opacity: 1; }
  .no-add-row .gantt_add { display: none !important; }

  /* ── 편집창(라이트박스) 디자인 ── */
  .gantt_cal_light {
    font-family: 'Noto Sans KR', sans-serif !important;
    border: none !important; border-radius: 14px;
    box-shadow: 0 20px 60px rgba(0,0,0,0.28);
    padding: 4px 8px 10px; box-sizing: border-box;
  }
  .gantt_cal_ltitle {
    border-radius: 14px 14px 0 0; border-bottom: 1px solid #f3f4f6;
    padding: 12px 14px 10px;
  }
  .gantt_cal_ltitle .gantt_title { font-weight: 700; font-size: 15px; color: #1a1a2e; }
  .gantt_cal_lsection {
    color: #6b7280 !important; font-weight: 600 !important;
    font-size: 12px !important; padding: 10px 12px 4px !important;
  }
  .gantt_cal_light input[type="text"], .gantt_cal_light textarea, .gantt_cal_light select {
    font-family: inherit; font-size: 13px;
    border: 1px solid #e5e7eb !important; border-radius: 8px;
    padding: 6px 9px; box-sizing: border-box; color: #1f2937;
  }
  .gantt_cal_light textarea { resize: none; overflow: auto; }
  .gantt_cal_light input[type="text"]:focus, .gantt_cal_light textarea:focus, .gantt_cal_light select:focus {
    outline: none; border-color: #4f46e5 !important; box-shadow: 0 0 0 3px rgba(79,70,229,0.12);
  }
  .gantt_cal_light .gantt_btn_set {
    border-radius: 8px; font-family: inherit; font-weight: 600;
    border: 1px solid #e5e7eb; background: #fff; transition: all 0.15s;
  }
  .gantt_cal_light .gantt_save_btn_set {
    background: #4f46e5; border-color: #4f46e5; color: #fff;
    box-shadow: 0 2px 8px rgba(79,70,229,0.3);
  }
  .gantt_cal_light .gantt_save_btn_set:hover { background: #4338ca; }
  .gantt_cal_light .gantt_cancel_btn_set:hover { border-color: #9ca3af; }
  .gantt_cal_light .gantt_delete_btn_set { border: none; background: transparent; color: #dc2626; }

  /* 시간 범위 드롭다운 */
  .timerange-block { display: flex; align-items: center; border: none !important; padding: 2px 0 !important; }
  .timerange-block select { width: 110px; }
  .timerange-block .tr-tilde { margin: 0 10px; color: #9ca3af; font-weight: 600; }

  /* 시작~종료 캘린더 범위 선택기 */
  .daterange-block { display: block !important; border: none !important; padding: 2px 0 !important; }
  .dr-top { display: flex; align-items: center; margin-bottom: 10px; }
  .daterange-block input[type="date"] {
    font-family: inherit; font-size: 13px; color: #1f2937;
    border: 1px solid #e5e7eb; border-radius: 8px; padding: 6px 9px;
  }
  .daterange-block input[type="date"]:focus {
    outline: none; border-color: #4f46e5; box-shadow: 0 0 0 3px rgba(79,70,229,0.12);
  }
  .daterange-block .dr-days { margin-left: 12px; font-size: 12px; color: #9ca3af; white-space: nowrap; }
  .dr-cal { display: flex; gap: 20px; }
  .dr-month { flex: 1; min-width: 0; }
  .dr-mhead {
    display: flex; justify-content: space-between; align-items: center;
    font-weight: 600; font-size: 12.5px; color: #374151; margin-bottom: 5px;
  }
  .dr-nav {
    border: 1px solid #e5e7eb; background: #fff; border-radius: 6px;
    width: 24px; height: 24px; cursor: pointer; color: #6b7280;
    font-size: 14px; line-height: 1; font-family: inherit;
  }
  .dr-nav:hover { color: #4f46e5; border-color: #4f46e5; }
  .dr-nav-sp { width: 24px; }
  .dr-grid { display: grid; grid-template-columns: repeat(7, 1fr); row-gap: 2px; }
  .dr-wd { text-align: center; font-size: 10px; color: #9ca3af; padding: 2px 0; }
  .dr-wd.sun { color: #dc2626; } .dr-wd.sat { color: #2563eb; }
  .dr-day {
    text-align: center; font-size: 12px; color: #374151;
    height: 26px; line-height: 26px; cursor: pointer; user-select: none;
  }
  .dr-day.empty { cursor: default; }
  .dr-day.sun { color: #dc2626; } .dr-day.sat { color: #2563eb; }
  .dr-day.hol { color: #dc2626; font-weight: 600; }
  .dr-day.today { font-weight: 700; text-decoration: underline; }
  .dr-day:not(.empty):hover { background: #eef2ff; border-radius: 7px; }
  .dr-day.in-range { background: #e0e7ff; color: #3730a3; }
  .dr-day.endpoint {
    background: #4f46e5; color: #fff !important; border-radius: 7px; font-weight: 700;
  }
  body.dark .daterange-block input[type="date"] {
    background: #0f172a; color: #e2e8f0; border-color: #334155; color-scheme: dark;
  }
  body.dark .dr-mhead { color: #cbd5e1; }
  body.dark .dr-nav { background: #0f172a; border-color: #334155; color: #94a3b8; }
  body.dark .dr-day { color: #cbd5e1; }
  body.dark .dr-day:not(.empty):hover { background: #1e1b4b; }
  body.dark .dr-day.in-range { background: #312e81; color: #c7d2fe; }

  /* 검색창 */
  .search-box {
    width: 150px; padding: 6px 12px; font-family: inherit; font-size: 12px;
    border: 1px solid #e5e7eb; border-radius: 8px; color: #1f2937;
    transition: all 0.15s; box-sizing: border-box;
  }
  .search-box:focus {
    outline: none; border-color: #4f46e5; box-shadow: 0 0 0 3px rgba(79,70,229,0.12); width: 200px;
  }
  body.dark .search-box { background: #0f172a; color: #e2e8f0; border-color: #334155; }

  /* 오늘 버튼 */
  .today-btn {
    border-color: #c7d2fe !important; color: #4f46e5 !important; font-weight: 700 !important;
  }
  .today-btn:hover { background: #eef2ff !important; }
  body.dark .today-btn { border-color: #4f46e5 !important; color: #a5b4fc !important; }
  body.dark .today-btn:hover { background: #1e1b4b !important; }

  /* 색상 견본 선택 */
  .colorpick-block { border: none !important; padding: 4px 0 !important; }
  .color-dot {
    display: inline-block; width: 24px; height: 24px; border-radius: 50%;
    margin: 5px 6px 0 0; cursor: pointer; vertical-align: middle;
    border: 2px solid rgba(0,0,0,0.08); transition: all 0.12s;
  }
  .color-dot:hover { transform: scale(1.15); }
  .color-dot.selected { box-shadow: 0 0 0 2px #fff, 0 0 0 4px #4f46e5; }
  .color-dot.color-auto {
    background: conic-gradient(#4f46e5, #0891b2, #059669, #d97706, #dc2626, #4f46e5);
    position: relative;
  }
  .color-dot.color-auto::after {
    content: "A"; position: absolute; inset: 0;
    display: flex; align-items: center; justify-content: center;
    color: #fff; font-size: 11px; font-weight: 700; text-shadow: 0 1px 2px rgba(0,0,0,0.45);
  }
  .color-dot.color-add {
    background: transparent; border: 2px dashed #cbd5e1; color: #9ca3af;
    text-align: center; line-height: 20px; font-weight: 700; font-size: 14px;
  }
  .color-dot.color-add:hover { border-color: #4f46e5; color: #4f46e5; }
  body.dark .color-dot.color-add { border-color: #475569; color: #64748b; }

  /* 완료 토글 뱃지 */
  .status-badge.clickable { cursor: pointer; }
  .status-badge.clickable:hover { box-shadow: 0 0 0 2px rgba(79,70,229,0.3); }

  /* 편집창 완료 처리 토글 */
  .done-block { display: flex; align-items: center; gap: 10px; border: none !important; padding: 4px 0 !important; }
  .done-toggle { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; font-size: 13px; color: #374151; }
  .done-toggle input { width: 16px; height: 16px; accent-color: #4f46e5; cursor: pointer; }
  .done-hint { font-size: 11.5px; color: #9ca3af; }
  body.dark .done-toggle { color: #cbd5e1; }

  /* ── 주간 브리핑 모달 ── */
  #briefOverlay {
    position: fixed; inset: 0; background: rgba(17,24,39,0.45); z-index: 500;
    display: none; align-items: center; justify-content: center;
  }
  #briefOverlay.show { display: flex; }
  #briefCard {
    background: #fff; border-radius: 16px; width: 560px; max-width: 92vw;
    max-height: 82vh; overflow-y: auto; padding: 24px 28px;
    box-shadow: 0 24px 70px rgba(0,0,0,0.35);
  }
  #briefCard h2 { margin: 0 0 2px; font-size: 18px; color: #1a1a2e; }
  #briefCard .brief-range { font-size: 12px; color: #9ca3af; margin-bottom: 14px; }
  #briefCard h3 { font-size: 13px; color: #4f46e5; margin: 14px 0 6px; }
  #briefCard ul { margin: 0; padding-left: 18px; }
  #briefCard li { font-size: 13px; color: #374151; margin: 3px 0; }
  #briefCard .brief-empty { font-size: 12px; color: #9ca3af; padding-left: 2px; }
  #briefCard .brief-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }

  /* ── 다크 모드 ── */
  body.dark { background: #0f172a; }
  body.dark .page-header { background: #1e293b; border-color: #334155; box-shadow: none; }
  body.dark .page-header h1 { color: #f1f5f9; }
  body.dark .page-header .subtitle { color: #64748b; }
  body.dark .btn-group { background: #0f172a; }
  body.dark .btn-group button { color: #94a3b8; }
  body.dark .btn-group button.active { background: #334155; color: #c7d2fe; box-shadow: none; }
  body.dark .tool-btn { background: #1e293b; border-color: #334155; color: #cbd5e1; }
  body.dark #saveBtn { background: #334155; color: #64748b; }
  body.dark #saveBtn.dirty { background: #4f46e5; color: #fff; }
  body.dark .gantt_container, body.dark .gantt_grid, body.dark .gantt_grid_scale,
  body.dark .gantt_task_scale, body.dark .gantt_grid_data { background: #0f172a; }
  body.dark .gantt_grid_scale .gantt_grid_head_cell, body.dark .gantt_scale_cell { color: #94a3b8 !important; }
  body.dark .gantt_task_row, body.dark .gantt_grid .gantt_row { background: #0f172a; border-color: #1e293b; }
  body.dark .gantt_grid .gantt_row.odd, body.dark .gantt_task_row.odd { background: #111a2e; }
  body.dark .gantt_cell, body.dark .gantt_tree_content { color: #cbd5e1; }
  body.dark .gantt_task_cell { border-color: #1e293b; }
  body.dark .weekend { background: #1a2438 !important; }
  body.dark .gantt_scale_cell.holiday { background: rgba(248,113,113,0.14) !important; color: #f87171 !important; }
  body.dark .gantt_task_cell.holiday { background: rgba(248,113,113,0.10) !important; }
  body.dark .gantt_task_cell.holiday.range-band { background: rgba(248,113,113,0.15) !important; }
  body.dark .gantt_task_cell.today { background: #29295e !important; }
  body.dark .gantt_grid_scale, body.dark .gantt_task_scale { border-color: #1e293b; }
  body.dark .gantt_layout_cell_border_right { border-color: #1e293b; }
  body.dark .project-row { background: #16223a !important; }
  body.dark .section-row { background: #1e1b4b !important; border-top-color: #3730a3 !important; }
  body.dark .section-row .gantt_tree_content { color: #a5b4fc !important; }
  body.dark .section-row-event { background: #382a10 !important; border-top-color: #92600e !important; }
  body.dark .section-row-event .gantt_tree_content { color: #fcd34d !important; }
  body.dark .past-group-row { background: #111a2e !important; }
  body.dark .done-row .gantt_tree_content, body.dark .done-row .gantt_cell { color: #475569 !important; }
  body.dark .gantt_tree_icon { filter: invert(0.8); }
  body.dark #briefCard { background: #1e293b; }
  body.dark #briefCard h2 { color: #f1f5f9; }
  body.dark #briefCard li { color: #cbd5e1; }
  body.dark .gantt_cal_light { background: #1e293b; color: #e2e8f0; }
  body.dark .gantt_cal_ltitle { background: #1e293b; border-color: #334155; }
  body.dark .gantt_cal_ltitle .gantt_title { color: #f1f5f9; }
  body.dark .gantt_cal_lsection { color: #94a3b8 !important; }
  body.dark .gantt_cal_light input[type="text"], body.dark .gantt_cal_light textarea,
  body.dark .gantt_cal_light select {
    background: #0f172a; color: #e2e8f0; border-color: #334155 !important;
  }
  body.dark .gantt_cal_light .gantt_btn_set { background: #0f172a; border-color: #334155; color: #cbd5e1; }
  body.dark .gantt_cal_light .gantt_save_btn_set { background: #4f46e5; color: #fff; }

  /* 프로젝트별 색상 (서버에서 자동 생성) */
{{COLOR_CSS}}
</style>
</head>
<body>

<div class="page-header">
  <div>
    <h1>업무 스케줄</h1>
    <div class="subtitle">드래그·더블클릭으로 바로 편집 · 마지막 업데이트 {{LAST_UPDATED}}</div>
  </div>
  <div class="header-controls">
    <input id="searchBox" class="search-box" type="search" placeholder="🔍 검색" oninput="onSearch(this.value)">
    <button class="tool-btn today-btn" onclick="goToday()">📍 오늘</button>
    <div class="btn-group">
      <button data-scale="day" class="active">일간</button>
      <button data-scale="week">주간</button>
      <button data-scale="month">월간</button>
    </div>
    <div class="btn-group">
      <button data-range="1" class="active">1개월</button>
      <button data-range="3">3개월</button>
      <button data-range="6">6개월</button>
      <button data-range="12">1년</button>
    </div>
    <div class="btn-group">
      <button onclick="collapseAll()">접기</button>
      <button onclick="expandAll()">펼치기</button>
      <button id="toggleDone" onclick="toggleDone()">완료 숨기기</button>
      <button id="darkBtn" onclick="toggleDark()" title="다크 모드">🌙</button>
    </div>
    <button class="tool-btn" onclick="showBriefing()">주간 브리핑</button>
    <button class="tool-btn" onclick="discardAll()">되돌리기</button>
    <button id="saveBtn" onclick="saveAll()">저장</button>
  </div>
</div>

<div id="gantt_here"></div>
<div id="toast"></div>

<div id="briefOverlay" onclick="if(event.target===this)this.classList.remove('show')">
  <div id="briefCard">
    <h2>주간 브리핑</h2>
    <div class="brief-range" id="briefRange"></div>
    <div id="briefBody"></div>
    <div class="brief-actions">
      <button class="tool-btn" onclick="copyBriefing()">복사</button>
      <button class="tool-btn" onclick="document.getElementById('briefOverlay').classList.remove('show')">닫기</button>
    </div>
  </div>
</div>

<script>
var COLOR_OPTS = {{COLOR_OPTS}};
var DATA_TOKEN = "{{DATA_TOKEN}}";
var APP_VER = "{{APP_VERSION}}";

gantt.locale = {
  date: {
    month_full: ["1월","2월","3월","4월","5월","6월","7월","8월","9월","10월","11월","12월"],
    month_short: ["1월","2월","3월","4월","5월","6월","7월","8월","9월","10월","11월","12월"],
    day_full: ["일요일","월요일","화요일","수요일","목요일","금요일","토요일"],
    day_short: ["일","월","화","수","목","금","토"]
  },
  labels: {
    new_task: "새 항목", icon_save: "저장", icon_cancel: "취소", icon_details: "상세",
    icon_edit: "편집", icon_delete: "삭제", confirm_closing: "",
    confirm_deleting: "정말 삭제하시겠습니까?",
    gantt_save_btn: "저장", gantt_cancel_btn: "취소", gantt_delete_btn: "삭제",
    section_description: "이름", section_time: "기간",
    section_evtime: "시간 (예: 10:00~11:30)",
    section_color: "색상 테마",
    section_donebox: "완료 처리",
    section_undetbox: "일정 확정 여부",
    section_notes: "메모",
    column_text: "업무명", column_start_date: "시작", column_duration: "기간", column_add: "",
    type_task: "업무", type_project: "프로젝트", type_milestone: "마일스톤",
    minutes: "분", hours: "시간", days: "일", weeks: "주", months: "월", years: "년"
  }
};

try { gantt.plugins({ tooltip: true, undo: true }); } catch(e) {}

gantt.config.date_format = "%d-%m-%Y %H:%i";
gantt.config.xml_date = "%d-%m-%Y %H:%i";
gantt.config.duration_unit = "day";
gantt.config.open_tree_initially = false;
gantt.config.show_progress = true;
gantt.config.row_height = 38;
gantt.config.bar_height = 24;
gantt.config.scale_height = 64;
gantt.config.min_column_width = 56;
gantt.config.readonly = false;
gantt.config.autofit = false;
gantt.config.drag_links = false;
gantt.config.show_links = false;
gantt.config.drag_progress = false;
gantt.config.order_branch = "marker";
gantt.config.order_branch_free = false;

// 상태 자동 판별 (시각은 무시하고 날짜만 비교)
function dateOnlyStatus(task) {
  if (task.is_section || task.is_past_group) return '';
  if (task.custom_status === 'undetermined') return 'undetermined';
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
function getTaskStatus(task) {
  if (task.is_section || task.is_past_group) return '';
  if (task.custom_status === 'undetermined') return 'undetermined';
  if (task.done) return 'completed';
  return dateOnlyStatus(task);
}
function escHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// 주말 제외 근무일 수 (시작일 포함, end 배타적)
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

gantt.config.columns = [
  { name: "text", label: "업무명", tree: true, width: 300, resize: true,
    editor: { type: "text", map_to: "text" } },
  { name: "status", label: "상태", align: "center", width: 60,
    template: function(task) {
      var st = getTaskStatus(task);
      // 잎 항목은 뱃지 클릭으로 완료 토글 가능
      var canToggle = !task.is_section && !task.is_past_group &&
                      task.type !== "project" && !gantt.hasChild(task.id);
      if (st === 'completed') {
        // 해제는 done 플래그로 완료된 경우에만 의미 있음 (날짜가 지난 항목은 해제해도 완료)
        if (task.done && canToggle && dateOnlyStatus(task) !== 'completed') {
          return '<span class="status-badge clickable" style="background:#e8efe9;color:#6b7280;" title="클릭: 완료 해제">완료</span>';
        }
        return '<span class="status-badge" style="background:#e8efe9;color:#6b7280;">완료</span>';
      }
      var toggleAttr = canToggle ? ' clickable" title="클릭: 완료로 표시' : '';
      if (st === 'in-progress') return '<span class="status-badge status-in-progress' + toggleAttr + '">진행중</span>';
      if (st === 'upcoming') return '<span class="status-badge status-upcoming' + toggleAttr + '">예정</span>';
      if (st === 'undetermined') return '<span class="status-badge status-undetermined">미정</span>';
      return '';
    }
  },
  { name: "start_date", label: "시작", align: "center", width: 96,
    template: function(task) {
      if (task.is_section || task.is_past_group || task.unscheduled || !task.start_date) return "";
      return gantt.templates.date_grid(task.start_date, task);
    }
  },
  { name: "duration", label: "기간(일)", align: "center", width: 70,
    template: function(task) {
      if (task.is_section || task.is_past_group || task.unscheduled) return "";
      if (task.is_single_event && task.start_date && task.end_date) {
        var d = Math.round((task.end_date - task.start_date) / 86400000);
        return d || 1;  // 일회성 일정은 주말 포함 달력일
      }
      if (task.start_date && task.end_date) return workDaysBetween(task.start_date, task.end_date);
      return "";
    }
  },
  { name: "notes", label: "메모", width: 160, resize: true,
    editor: { type: "text", map_to: "notes" },
    template: function(task) {
      if (!task.notes) return '';
      var short = task.notes.replace(/\n/g, ' ');
      if (short.length > 25) short = short.substring(0, 25) + '...';
      return '<span style="font-size:11px;color:#6b7280" title="' + escHtml(task.notes).replace(/\n/g, '&#10;') + '">' + escHtml(short) + '</span>';
    }
  },
  { name: "add", width: 40 }
];

// 인라인 편집: 섹션·지난그룹 행은 제외
try {
  gantt.ext.inlineEditors.attachEvent("onBeforeEditStart", function(state) {
    var t = gantt.getTask(state.id);
    return !(t.is_section || t.is_past_group);
  });
} catch(e) {}

// 열 너비 기억
try {
  var savedW = JSON.parse(localStorage.getItem('colWidths') || "{}");
  gantt.config.columns.forEach(function(c) { if (savedW[c.name]) c.width = savedW[c.name]; });
} catch(e) {}
gantt.attachEvent("onColumnResizeEnd", function(ind, column, newWidth) {
  if (column && column.name && newWidth > 20) {
    try {
      var w = JSON.parse(localStorage.getItem('colWidths') || "{}");
      w[column.name] = newWidth;
      localStorage.setItem('colWidths', JSON.stringify(w));
    } catch(e) {}
  }
  return true;
});

gantt.config.scales = [
  { unit: "month", step: 1, format: "%Y년 %M" },
  { unit: "day", step: 1, format: function(date) { return dayScaleFormat(date); } }
];
var scaleConfigs = {
  day:   { scale_height: 64, min_column_width: 56, scales: [ { unit: "month", step: 1, format: "%Y년 %M" }, { unit: "day", step: 1, format: function(date) { return dayScaleFormat(date); } } ] },
  week:  { scale_height: 56, min_column_width: 80, scales: [ { unit: "month", step: 1, format: "%Y년 %M" }, { unit: "week", step: 1, format: "%W주차" } ] },
  month: { scale_height: 56, min_column_width: 60, scales: [ { unit: "year", step: 1, format: "%Y년" }, { unit: "month", step: 1, format: "%M" } ] }
};

var currentScale = 'day', currentRange = 1;
function applyScaleCfg(level) {
  var cfg = scaleConfigs[level];
  gantt.config.scale_height = cfg.scale_height;
  gantt.config.min_column_width = cfg.min_column_width;
  gantt.config.scales = cfg.scales;
  currentScale = level;
}
function setScale(level) {
  applyScaleCfg(level);
  gantt.render();
}

// 주말 + 공휴일
var HOLIDAYS = {{HOLIDAYS}};  // {날짜: 이름}
function holidayName(date) {
  var k = date.getFullYear() + "-" + ("0" + (date.getMonth() + 1)).slice(-2) + "-" + ("0" + date.getDate()).slice(-2);
  return HOLIDAYS[k] || "";
}
function isHoliday(date) { return !!holidayName(date); }
function dayScaleFormat(date) {
  var h = holidayName(date);
  if (h) return "<div class='hd'><span class='hd-d'>" + date.getDate() + "</span><span class='hd-n'>" + h + "</span></div>";
  return date.getDate() + "(" + gantt.locale.date.day_short[date.getDay()] + ")";
}
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
  return cls;
};

gantt.templates.grid_row_class = function(start, end, task) {
  var cls = [];
  if (task.is_section === 'project') cls.push("section-row");
  else if (task.is_section === 'event') cls.push("section-row-event");
  else if (task.is_parent_project) cls.push("project-row");
  if (task.is_past_group) cls.push("past-group-row");
  if (task.is_sub_project) cls.push("sub-project-row");
  if (task.is_single_event) cls.push("single-event-row");
  if (task.custom_status === 'undetermined') cls.push("undetermined-row");
  if (getTaskStatus(task) === 'completed') {
    cls.push("done-row");
    if (task.bar_level === 3) cls.push("leaf-row");
  }
  // + 버튼은 하위 항목을 가질 수 있는 행에만 (업무·일정·지난그룹 제외)
  if (task.kind === "task" || task.kind === "event" || task.is_past_group) cls.push("no-add-row");
  return cls.join(" ");
};
gantt.templates.task_row_class = function(start, end, task) {
  var cls = [];
  if (task.is_section === 'project') cls.push("section-row");
  else if (task.is_section === 'event') cls.push("section-row-event");
  if (task.is_single_event) cls.push("single-event-row");
  if (task.custom_status === 'undetermined') cls.push("undetermined-row");
  return cls.join(" ");
};

gantt.templates.task_text = function(start, end, task) { return ""; };
gantt.templates.lightbox_header = function(start, end, task) {
  return (task && task.text) ? escHtml(task.text) : "새 항목";
};
// 편집창 날짜 표기: 연·월·일 (마지막 날 기준으로 표시)
gantt.config.task_date = "%Y년 %n월 %j일 (%D)";
gantt.templates.task_date = gantt.date.date_to_str("%Y년 %n월 %j일 (%D)");
gantt.templates.task_end_date = function(date) {
  return gantt.templates.task_date(new Date(date.valueOf() - 86400000));
};
gantt.templates.rightside_text = function(start, end, task) {
  if (task.type === "milestone") {
    return escHtml(task.text) + (task.custom_time ? " · " + escHtml(task.custom_time) : "");
  }
  return "";
};
gantt.templates.task_class = function(start, end, task) {
  if (task.is_section || task.is_past_group) return "hide-bar";
  var cls = task.color_class || "";
  if (task.bar_level === 1) cls += " lv-project";
  else if (task.bar_level === 2) cls += " lv-subproject";
  else if (task.type !== "milestone") cls += " lv-task";
  if (task.custom_status === 'undetermined') cls += " hide-bar";
  if (getTaskStatus(task) === 'completed') cls += " is-done";
  return cls;
};

try {
  gantt.templates.tooltip_date_format = gantt.date.date_to_str("%Y-%m-%d");
  gantt.templates.tooltip_text = function(start, end, task) {
    if (task.is_section || task.is_past_group) return "";
    var h = "<b>" + escHtml(task.text) + "</b><br>";
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
} catch(e) {}

// ── 커스텀 컨트롤: 시작~종료 캘린더 날짜 선택 ──
function isoToDate(v) {
  if (!v) return null;
  var p = v.split("-");
  return new Date(+p[0], +p[1] - 1, +p[2]);
}
function dateToIso(d) {
  return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2);
}
// 달력 범위 선택기: 시작일 클릭 → 종료일 클릭, 사이 기간이 색으로 칠해짐
function drMonthHtml(first, sel, idx) {
  var y = first.getFullYear(), mo = first.getMonth();
  var nav0 = idx === 0 ? "<button type='button' class='dr-nav' data-d='-1'>&#8249;</button>" : "<span class='dr-nav-sp'></span>";
  var nav1 = idx === 1 ? "<button type='button' class='dr-nav' data-d='1'>&#8250;</button>" : "<span class='dr-nav-sp'></span>";
  var html = "<div class='dr-month'><div class='dr-mhead'>" + nav0 +
             "<span>" + y + "년 " + (mo + 1) + "월</span>" + nav1 + "</div><div class='dr-grid'>";
  ["일", "월", "화", "수", "목", "금", "토"].forEach(function(w, i) {
    html += "<span class='dr-wd" + (i === 0 ? " sun" : (i === 6 ? " sat" : "")) + "'>" + w + "</span>";
  });
  var startWd = new Date(y, mo, 1).getDay();
  var days = new Date(y, mo + 1, 0).getDate();
  for (var i = 0; i < startWd; i++) html += "<span class='dr-day empty'></span>";
  var today = new Date(); today.setHours(0, 0, 0, 0);
  for (var d = 1; d <= days; d++) {
    var dt = new Date(y, mo, d);
    var iso = dateToIso(dt);
    var cls = "dr-day";
    if (dt.getDay() === 0) cls += " sun";
    if (dt.getDay() === 6) cls += " sat";
    if (HOLIDAYS[iso]) cls += " hol";
    if (dt.getTime() === today.getTime()) cls += " today";
    var s = sel.s, e = sel.e;
    var isS = s && dt.getTime() === s.getTime();
    var isE = e && dt.getTime() === e.getTime();
    if (isS || isE) cls += " endpoint";
    else if (s && e && dt > s && dt < e) cls += " in-range";
    html += "<span class='" + cls + "' data-iso='" + iso + "'" +
            (HOLIDAYS[iso] ? " title='" + HOLIDAYS[iso] + "'" : "") + ">" + d + "</span>";
  }
  return html + "</div></div>";
}
function drRender(node) {
  var sel = node._sel;
  var lbl = node.querySelector(".dr-days");
  var s = sel.s, e = sel.e || sel.s;
  if (s && e) {
    var endEx = new Date(e); endEx.setDate(endEx.getDate() + 1);
    lbl.textContent = "근무일 " + workDaysBetween(s, endEx) + "일";
  } else {
    lbl.textContent = sel.s ? "종료일을 선택하세요" : "";
  }
  var host = node.querySelector(".dr-cal");
  var html = "";
  for (var m = 0; m < 2; m++) {
    html += drMonthHtml(new Date(sel.view.getFullYear(), sel.view.getMonth() + m, 1), sel, m);
  }
  host.innerHTML = html;
}
function drSync(node) {
  var sel = node._sel;
  node.querySelector(".dr-start").value = sel.s ? dateToIso(sel.s) : "";
  node.querySelector(".dr-end").value = (sel.e || sel.s) ? dateToIso(sel.e || sel.s) : "";
  drRender(node);
}
gantt.form_blocks["daterange"] = {
  render: function(sns) {
    return "<div class='gantt_cal_ltext daterange-block'>" +
      "<div class='dr-top'>" +
        "<input type='date' class='dr-start'>" +
        "<span class='tr-tilde'>~</span>" +
        "<input type='date' class='dr-end'>" +
        "<span class='dr-days'></span>" +
      "</div>" +
      "<div class='dr-cal'></div></div>";
  },
  set_value: function(node, value, task) {
    var s = task.start_date ? new Date(task.start_date) : new Date();
    s.setHours(0, 0, 0, 0);
    var last = task.end_date
      ? new Date(task.end_date.valueOf() - (task.type === "milestone" ? 0 : 86400000))
      : new Date(s);
    last.setHours(0, 0, 0, 0);
    if (last < s) last = new Date(s);
    node._sel = { s: s, e: last, view: new Date(s.getFullYear(), s.getMonth(), 1) };
    if (!node._wired) {
      node._wired = true;
      var si = node.querySelector(".dr-start"), ei = node.querySelector(".dr-end");
      si.addEventListener("change", function() {
        var d = isoToDate(si.value);
        if (!d) return;
        node._sel.s = d;
        if (node._sel.e && node._sel.e < d) node._sel.e = d;
        node._sel.view = new Date(d.getFullYear(), d.getMonth(), 1);
        drSync(node);
      });
      ei.addEventListener("change", function() {
        var d = isoToDate(ei.value);
        if (!d) return;
        node._sel.e = d;
        if (node._sel.s && d < node._sel.s) node._sel.s = d;
        drSync(node);
      });
      node.querySelector(".dr-cal").addEventListener("click", function(ev) {
        var nav = ev.target.closest(".dr-nav");
        if (nav) {
          node._sel.view = new Date(node._sel.view.getFullYear(), node._sel.view.getMonth() + parseInt(nav.dataset.d), 1);
          drRender(node);
          return;
        }
        var cell = ev.target.closest(".dr-day[data-iso]");
        if (!cell) return;
        var d = isoToDate(cell.dataset.iso);
        var sel = node._sel;
        if (!sel.s || sel.e || d < sel.s) { sel.s = d; sel.e = null; }  // 새 범위 시작
        else sel.e = d;                                                  // 종료일 확정
        drSync(node);
      });
    }
    drSync(node);
  },
  get_value: function(node, task) {
    // 달력 선택 상태(_sel)를 우선 사용 — 입력칸이 비워져 있어도 화면 표시와 일치
    var sel = node._sel || {};
    var s = sel.s || isoToDate(node.querySelector(".dr-start").value) || new Date();
    var e = sel.e || sel.s || isoToDate(node.querySelector(".dr-end").value) || new Date(s);
    s = new Date(s); e = new Date(e);
    s.setHours(0, 0, 0, 0);
    e.setHours(0, 0, 0, 0);
    if (e < s) e = new Date(s);
    var endEx = new Date(e);
    endEx.setDate(endEx.getDate() + 1);  // gantt end_date는 배타적
    return { start_date: s, end_date: endEx };
  },
  focus: function(node) {}
};

// ── 커스텀 컨트롤: 시간 범위 드롭다운 (30분 단위) ──
gantt.form_blocks["timerange"] = {
  render: function(sns) {
    var opts = '<option value="">--:--</option>';
    for (var h = 7; h <= 22; h++) {
      for (var m = 0; m < 60; m += 30) {
        var t = ("0" + h).slice(-2) + ":" + ("0" + m).slice(-2);
        opts += '<option value="' + t + '">' + t + '</option>';
      }
    }
    return "<div class='gantt_cal_ltext timerange-block'>" +
      "<select class='tr-start'>" + opts + "</select>" +
      "<span class='tr-tilde'>~</span>" +
      "<select class='tr-end'>" + opts + "</select></div>";
  },
  set_value: function(node, value, task) {
    var s = "", e = "";
    if (value) { var p = String(value).split("~"); s = (p[0] || "").trim(); e = (p[1] || "").trim(); }
    ["tr-start", "tr-end"].forEach(function(cls, i) {
      var sel = node.querySelector("." + cls);
      var v = i === 0 ? s : e;
      if (v && !sel.querySelector('option[value="' + v + '"]')) {
        var o = document.createElement("option");
        o.value = v; o.textContent = v;
        sel.appendChild(o);  // 목록에 없는 기존 값 보존
      }
      sel.value = v;
    });
  },
  get_value: function(node) {
    var s = node.querySelector(".tr-start").value;
    var e = node.querySelector(".tr-end").value;
    if (s && e) return s + "~" + e;
    if (e) return "~" + e;  // 종료만 선택 시 시작으로 둔갑하지 않도록
    return s || "";
  },
  focus: function(node) {}
};

// ── 커스텀 컨트롤: 색상 견본 선택 (+ 새 색상 추가) ──
var NEW_COLORS = {};  // 이번 세션에서 추가한 색 (저장 시 팔레트에 병합)
function darkenHex(hex) {
  var n = parseInt(hex.slice(1), 16);
  var r = Math.round(((n >> 16) & 255) * 0.78);
  var g = Math.round(((n >> 8) & 255) * 0.78);
  var b = Math.round((n & 255) * 0.78);
  return "#" + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
}
function nextColorKey() {
  var i = 1;
  while (COLOR_OPTS.some(function(c) { return c.key === "c" + i; })) i++;
  return "c" + i;
}
gantt.form_blocks["colorpick"] = {
  render: function(sns) {
    var html = "<div class='gantt_cal_ltext colorpick-block'>";
    COLOR_OPTS.forEach(function(c) {
      if (!c.key) {
        html += "<span class='color-dot color-auto' data-key='' title='자동 — 저장 시 가장 적게 쓰인 색이 배정됩니다'></span>";
      } else {
        html += "<span class='color-dot' data-key='" + c.key + "' title='" + c.key +
                "' style='background:" + (c.bg || "#9ca3af") + "'></span>";
      }
    });
    html += "<span class='color-dot color-add' title='새 색상 추가'>+</span>" +
            "<input type='color' class='color-input' value='#7c3aed' style='display:none'></div>";
    return html;
  },
  set_value: function(node, value, task) {
    node.querySelectorAll(".color-dot:not(.color-add)").forEach(function(d) {
      d.classList.toggle("selected", d.dataset.key === (value || ""));
    });
    if (!node._wired) {
      node._wired = true;
      var input = node.querySelector(".color-input");
      node.addEventListener("click", function(ev) {
        var dot = ev.target.closest(".color-dot");
        if (!dot) return;
        if (dot.classList.contains("color-add")) { input.click(); return; }
        node.querySelectorAll(".color-dot").forEach(function(d) { d.classList.remove("selected"); });
        dot.classList.add("selected");
      });
      input.addEventListener("change", function() {
        var bg = input.value, key = nextColorKey();
        NEW_COLORS[key] = { bg: bg, border: darkenHex(bg) };
        COLOR_OPTS.push({ key: key, bg: bg });
        var dot = document.createElement("span");
        dot.className = "color-dot selected";
        dot.dataset.key = key;
        dot.title = key;
        dot.style.background = bg;
        node.querySelectorAll(".color-dot").forEach(function(d) { d.classList.remove("selected"); });
        node.insertBefore(dot, node.querySelector(".color-add"));
        dot.classList.add("selected");
        showToast("새 색상이 추가되었습니다 (저장 후 바에 반영)");
      });
    }
  },
  get_value: function(node) {
    var sel = node.querySelector(".color-dot.selected");
    return sel ? sel.dataset.key : "";
  },
  focus: function(node) {}
};

// ── 커스텀 컨트롤: 완료 처리 토글 ──
gantt.form_blocks["donetoggle"] = {
  render: function(sns) {
    return "<div class='gantt_cal_ltext done-block'>" +
      "<label class='done-toggle'><input type='checkbox' class='done-check'>" +
      "<span>이 항목을 완료로 표시</span></label>" +
      "<span class='done-hint'></span></div>";
  },
  set_value: function(node, value, task) {
    node.querySelector(".done-check").checked = !!value;
    var hint = node.querySelector(".done-hint");
    hint.textContent = (dateOnlyStatus(task) === "completed")
      ? "(날짜가 지나 이미 완료 상태입니다)" : "";
  },
  get_value: function(node, task) {
    return node.querySelector(".done-check").checked;
  },
  focus: function(node) {}
};

// ── 커스텀 컨트롤: 날짜 미정 토글 ──
gantt.form_blocks["undettoggle"] = {
  render: function(sns) {
    return "<div class='gantt_cal_ltext done-block'>" +
      "<label class='done-toggle'><input type='checkbox' class='undet-check'>" +
      "<span>날짜 미정 (기간은 예상 일정으로만 보관)</span></label></div>";
  },
  set_value: function(node, value, task) {
    node.querySelector(".undet-check").checked = (value === "undetermined");
  },
  get_value: function(node, task) {
    return node.querySelector(".undet-check").checked ? "undetermined" : "";
  },
  focus: function(node) {}
};

// ── 라이트박스: 항목 종류에 따라 필드 구성 ──
function setLightbox(kind, hasKids) {
  var secs = [
    { name: "description", height: 34, map_to: "text", type: "textarea", focus: true }
  ];
  // 자식이 있는 항목의 기간은 하위 일정으로 자동 계산 — 직접 입력을 받지 않음
  // (입력해도 저장 시 롤업에 밀려 사라지는 무경고 유실 방지)
  if (!hasKids) secs.push({ name: "time", height: 262, type: "daterange", map_to: "auto" });
  if (kind === "event") secs.push({ name: "evtime", height: 40, map_to: "custom_time", type: "timerange" });
  if (kind === "event" || kind === "project") secs.push({ name: "color", height: 44, map_to: "color_key", type: "colorpick" });
  if (!hasKids) secs.push({ name: "donebox", height: 34, map_to: "done", type: "donetoggle" });
  if (!hasKids && kind !== "event") secs.push({ name: "undetbox", height: 34, map_to: "custom_status", type: "undettoggle" });
  secs.push({ name: "notes", height: 72, map_to: "notes", type: "textarea" });
  gantt.config.lightbox.sections = secs;
  gantt.config.lightbox.project_sections = secs;
  gantt.config.lightbox.milestone_sections = secs;
  gantt.resetLightbox();
}

gantt.attachEvent("onBeforeLightbox", function(id) {
  var t = gantt.getTask(id);
  if (t.is_section || t.is_past_group) return false;
  t.notes = t.notes || "";
  t.custom_time = t.custom_time || "";
  t.color_key = t.color_key || "";
  setLightbox(t.kind || "task", gantt.hasChild(id));
  return true;
});

// 마일스톤은 항상 정오에 위치 (날짜 칸 정중앙)
function fixNoon(task) {
  if (task.type === "milestone" && task.start_date && task.start_date.getHours() === 0) {
    task.start_date.setHours(12);
    task.end_date = new Date(task.start_date);
  }
}
// 일회성 일정: 하루짜리 = 다이아몬드, 여러 날 = 기간 바 (끝 날짜에 따라 자동 전환)
function normalizeEventType(task) {
  if (task.kind !== "event" || !task.start_date) return;
  var end = task.end_date || task.start_date;
  var days = Math.round((end - task.start_date) / 86400000);
  if (days > 1) {
    task.type = "task";
    task.start_date.setHours(0, 0, 0, 0);
  } else {
    task.type = "milestone";
    task.start_date.setHours(12, 0, 0, 0);
    task.end_date = new Date(task.start_date);
  }
}
gantt.attachEvent("onAfterTaskDrag", function(id) {
  var t = gantt.getTask(id);
  if (t.kind === "event") normalizeEventType(t);
  else fixNoon(t);
  gantt.refreshTask(id);
});
gantt.attachEvent("onLightboxSave", function(id, task) {
  // 잎 항목: 날짜가 지정되므로 unscheduled 해제.
  // '미정' 여부는 편집창의 체크박스(custom_status 매핑)를 그대로 따른다.
  if (!gantt.hasChild(id)) {
    task.unscheduled = false;
    if (!task.custom_status) delete task.custom_status;
  }
  if (task.kind === "event") normalizeEventType(task);
  else fixNoon(task);
  return true;
});

// ── 상태 뱃지 클릭 = 완료 토글 ──
gantt.attachEvent("onTaskClick", function(id, e) {
  var badge = e.target.closest(".status-badge");
  if (badge && badge.classList.contains("clickable")) {
    var t = gantt.getTask(id);
    if (t.done) delete t.done;
    else t.done = true;
    gantt.updateTask(id);
    return false;
  }
  return true;
});
// 뱃지 더블클릭이 편집창까지 열지 않도록
gantt.attachEvent("onTaskDblClick", function(id, e) {
  if (e && e.target && e.target.closest && e.target.closest(".status-badge")) return false;
  return true;
});

// ── 섹션/그룹 행 보호 ──
function isProtected(t) { return t.is_section || t.is_past_group; }
gantt.attachEvent("onBeforeTaskDrag", function(id) { return !isProtected(gantt.getTask(id)); });
gantt.attachEvent("onBeforeTaskDelete", function(id) {
  if (isProtected(gantt.getTask(id))) { gantt.message({ type: "error", text: "섹션은 삭제할 수 없습니다" }); return false; }
  return true;
});
gantt.attachEvent("onBeforeRowDragMove", function(id) { return !isProtected(gantt.getTask(id)); });
gantt.attachEvent("onBeforeRowDragEnd", function(id, parent, tindex) {
  var t = gantt.getTask(id);
  if (t.parent != parent) { gantt.message({ type: "error", text: "같은 그룹 안에서만 순서를 바꿀 수 있습니다" }); return false; }
  return true;
});

// ── 새 항목 추가 규칙 ──
gantt.attachEvent("onTaskCreated", function(task) {
  var pid = task.parent;
  if (!pid || pid == 0) { gantt.message({ type: "error", text: "섹션 안의 + 버튼으로 추가해주세요" }); return false; }
  var p = gantt.getTask(pid);
  // 기본 날짜: 오늘 하루
  var d0 = new Date(); d0.setHours(0, 0, 0, 0);
  var d1 = new Date(d0); d1.setDate(d1.getDate() + 1);
  task.start_date = d0;
  task.end_date = d1;
  if (p.is_section === "event" || p.is_past_group) {
    task.kind = "event"; task.text = "새 일정"; task.duration = 1;
    task.custom_time = ""; task.is_single_event = true; task.bar_level = 3;
  }
  else if (p.is_section === "project") { task.kind = "project"; task.text = "새 프로젝트"; task.bar_level = 3; }
  else if (p.kind === "project") { task.kind = "sub"; task.text = "새 세부 프로젝트"; task.color_class = p.color_class || ""; task.bar_level = 3; }
  else if (p.kind === "sub") { task.kind = "task"; task.text = "새 업무"; task.color_class = p.color_class || ""; task.bar_level = 3; }
  else { gantt.message({ type: "error", text: "업무/일정 아래에는 추가할 수 없습니다" }); return false; }
  task.notes = "";
  return true;
});

gantt.init("gantt_here");
gantt.parse({ data: {{GANTT_DATA}}, links: [] });

// 오늘 표시선
gantt.attachEvent("onGanttRender", function() {
  var areaEl = document.querySelector(".gantt_task");
  if (!areaEl) return;
  var old = document.getElementById("today_line");
  if (old) old.remove();
  var pos = gantt.posFromDate(new Date());
  if (pos > 0) {
    var line = document.createElement("div");
    line.id = "today_line";
    line.style.cssText = "position:absolute;top:0;left:" + pos + "px;width:2px;height:100%;background:#4f46e5;opacity:0.5;z-index:5;pointer-events:none;";
    areaEl.appendChild(line);
  }
});

// ── 완료 숨기기 ──
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
// 검색 필터: 자신 또는 하위 항목의 이름·메모가 일치하면 표시
var searchQ = "";
function onSearch(v) {
  searchQ = (v || "").trim().toLowerCase();
  gantt.render();
}
function textMatch(t) {
  return ((t.text || "") + " " + (t.notes || "")).toLowerCase().indexOf(searchQ) !== -1;
}
function subtreeMatch(t) {
  if (textMatch(t)) return true;
  var kids = gantt.getChildren(t.id) || [];
  for (var i = 0; i < kids.length; i++) {
    if (subtreeMatch(gantt.getTask(kids[i]))) return true;
  }
  return false;
}
function taskMatchesSearch(t) {
  if (subtreeMatch(t)) return true;
  // 조상이 매치되면 하위 항목도 함께 표시 (프로젝트명 검색 시 내용이 비지 않도록)
  var p = t.parent;
  while (p && p != gantt.config.root_id && gantt.isTaskExists(p)) {
    var pt = gantt.getTask(p);
    if (textMatch(pt)) return true;
    p = pt.parent;
  }
  return false;
}

gantt.attachEvent("onBeforeTaskDisplay", function(id, task) {
  // 검색 중에는 검색 결과가 우선 (지난 일정·완료 숨기기보다 먼저 — 검색 누락 방지)
  if (searchQ) return taskMatchesSearch(task);
  if (!hideDone) return true;
  if (task.is_past_group) return false;
  var t = task;
  while (t) {
    if ((t.is_parent_project || t.is_single_event) && getTaskStatus(t) === 'completed') return false;
    var p = t.parent;
    t = (p && p != gantt.config.root_id && gantt.isTaskExists(p)) ? gantt.getTask(p) : null;
  }
  return true;
});

// ── 범위 조절 ──
var rangeScaleMap = { 1: 'day', 3: 'week', 6: 'week', 12: 'month' };
function setRange(months) {
  var autoScale = rangeScaleMap[months] || 'day';
  applyScaleCfg(autoScale);
  currentRange = months;
  gantt.config.start_date = null;
  gantt.config.end_date = null;
  document.querySelectorAll('[data-scale]').forEach(function(b) { b.classList.remove('active'); });
  var activeBtn = document.querySelector('[data-scale="' + autoScale + '"]');
  if (activeBtn) activeBtn.classList.add('active');
  gantt.render();
  gantt.showDate(new Date());
}

// ── 저장/새로고침 후에도 보던 화면 유지 ──
function snapshotView() {
  try {
    var open = {};
    gantt.eachTask(function(t) { open[t.id] = !!t.$open; });
    sessionStorage.setItem('viewState', JSON.stringify({
      open: open,
      scroll: gantt.getScrollState(),
      scale: currentScale,
      range: currentRange
    }));
  } catch(e) {}
}
function restoreView(vs) {
  applyScaleCfg(vs.scale || 'day');
  currentRange = vs.range || 1;
  document.querySelectorAll('[data-scale]').forEach(function(b) {
    b.classList.toggle('active', b.dataset.scale === currentScale);
  });
  document.querySelectorAll('[data-range]').forEach(function(b) {
    b.classList.toggle('active', parseInt(b.dataset.range) === currentRange);
  });
  if (vs.open) {
    gantt.eachTask(function(t) {
      if (Object.prototype.hasOwnProperty.call(vs.open, t.id)) t.$open = vs.open[t.id];
    });
  }
  gantt.render();
  if (vs.scroll) setTimeout(function() { gantt.scrollTo(vs.scroll.x, vs.scroll.y); }, 30);
}
var savedView = null;
try {
  savedView = JSON.parse(sessionStorage.getItem('viewState') || "null");
  sessionStorage.removeItem('viewState');
} catch(e) {}
if (savedView) restoreView(savedView);
else setRange(1);

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

// ── 변경 감지 · 저장 · 토스트 ──
var dirty = false;
function markDirty() {
  dirty = true;
  var b = document.getElementById("saveBtn");
  b.classList.add("dirty");
  b.textContent = "저장 *";
}
gantt.attachEvent("onAfterTaskUpdate", markDirty);
gantt.attachEvent("onRowDragEnd", markDirty);
// $new = 라이트박스에서 아직 확정 안 된 임시 항목 (추가 후 취소 시 dirty가 남지 않도록 제외)
gantt.attachEvent("onAfterTaskAdd", function(id, task) { if (!task.$new) markDirty(); });
gantt.attachEvent("onAfterTaskDelete", function(id, task) { if (!task.$new) markDirty(); });
window.addEventListener("beforeunload", function(e) {
  if (dirty) { e.preventDefault(); e.returnValue = ""; }
});

function showToast(msg) {
  var t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(function() { t.classList.remove("show"); }, 2200);
}

function saveAll() {
  if (!dirty) return;
  fetch("/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tasks: gantt.serialize().data, colors: NEW_COLORS,
                           data_token: DATA_TOKEN, app_version: APP_VER })
  }).then(function(r) { return r.json(); }).then(function(res) {
    if (res.ok) {
      dirty = false;
      if (res.warn) alert(res.warn);  // 저장은 성공, 재빌드만 실패한 경우
      showToast("저장 완료");
      snapshotView();
      setTimeout(function() { location.reload(); }, 500);
    } else {
      alert("저장 실패: " + res.error);
    }
  }).catch(function(e) {
    alert("저장 실패 (서버 연결 확인): " + e);
  });
}
function discardAll() {
  if (!dirty) { showToast("변경사항이 없습니다"); return; }
  if (confirm("저장하지 않은 변경사항을 버리고 다시 불러올까요?")) {
    dirty = false;
    snapshotView();
    location.reload();
  }
}

// ── 단축키: Ctrl+S 저장, Ctrl+Z/Y 실행취소, Delete 삭제 ──
function goToday() { gantt.showDate(new Date()); }
function inEditing(e) {
  var tag = (e.target.tagName || "").toUpperCase();
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" ||
         !!gantt.getState().lightbox;
}
document.addEventListener("keydown", function(e) {
  if (e.key === "Escape") { document.getElementById("briefOverlay").classList.remove("show"); return; }
  if (inEditing(e)) return;  // 편집 중에는 단축키 무시 (Ctrl+S로 미완성 내용이 저장·리로드되는 사고 방지)
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); saveAll(); return; }
  if ((e.ctrlKey || e.metaKey) && (e.key === "z" || e.key === "Z")) {
    e.preventDefault(); try { gantt.undo(); showToast("실행 취소"); } catch(x) {}
    return;
  }
  if ((e.ctrlKey || e.metaKey) && (e.key === "y" || e.key === "Y")) {
    e.preventDefault(); try { gantt.redo(); showToast("다시 실행"); } catch(x) {}
    return;
  }
  if (e.key === "Delete") {
    var sel = gantt.getState().selected_task;
    if (sel && gantt.isTaskExists(sel)) {
      var t = gantt.getTask(sel);
      if (!t.is_section && !t.is_past_group && confirm('"' + t.text + '" 항목을 삭제할까요?')) {
        gantt.deleteTask(sel);
      }
    }
  }
});

// ── 다크 모드 ──
var darkMode = localStorage.getItem('darkMode') === '1';
function applyDark() {
  document.body.classList.toggle('dark', darkMode);
  var b = document.getElementById('darkBtn');
  if (b) { b.textContent = darkMode ? '☀️' : '🌙'; b.classList.toggle('active', darkMode); }
}
function toggleDark() {
  darkMode = !darkMode;
  localStorage.setItem('darkMode', darkMode ? '1' : '0');
  applyDark();
}
applyDark();

// ── 주간 브리핑 ──
var DAY_KO = ["일", "월", "화", "수", "목", "금", "토"];
function weekRange(offset) {
  var now = new Date(); now.setHours(0, 0, 0, 0);
  var mon = new Date(now);
  mon.setDate(now.getDate() - ((now.getDay() + 6) % 7) + offset * 7);
  var sun = new Date(mon); sun.setDate(mon.getDate() + 6);
  return [mon, sun];
}
function fmtMD(d) { return (d.getMonth() + 1) + "/" + d.getDate() + "(" + DAY_KO[d.getDay()] + ")"; }

var briefText = "";
function buildBriefing() {
  var thisW = weekRange(0), lastW = weekRange(-1);
  var today = new Date(); today.setHours(0, 0, 0, 0);
  var events = [], deadlines = [], ongoing = [], starting = [], doneLast = [];

  gantt.eachTask(function(t) {
    if (t.is_section || t.is_past_group || !t.start_date) return;
    if (t.type === "project" || gantt.hasChild(t.id)) return;  // 잎 항목만
    var s = new Date(t.start_date); s.setHours(0, 0, 0, 0);
    var last = t.end_date ? new Date(t.end_date.valueOf() - (t.type === "milestone" ? 0 : 86400000)) : new Date(s);
    last.setHours(0, 0, 0, 0);
    var st = getTaskStatus(t);

    if (t.is_single_event) {
      if (st === 'completed') {
        if (last >= lastW[0] && last <= lastW[1]) doneLast.push(t.text);
        return;
      }
      // 여러 날짜리 일정도 이번 주와 겹치면 포함
      if (s <= thisW[1] && last >= thisW[0]) {
        events.push({ d: s, txt: fmtMD(s) + (t.custom_time ? " " + t.custom_time : "") + " — " + t.text });
      }
      return;
    }
    if (st === 'completed') {
      if (last >= lastW[0] && last <= lastW[1]) doneLast.push(t.text);
      return;
    }
    if (st === 'undetermined') return;
    if (last >= thisW[0] && last <= thisW[1]) deadlines.push({ d: last, txt: t.text + " (~" + fmtMD(last) + ")" });
    else if (s <= today && last >= today) ongoing.push({ d: last, txt: t.text + " (~" + fmtMD(last) + ")" });
    else if (s > today && s <= thisW[1]) starting.push({ d: s, txt: t.text + " (" + fmtMD(s) + " 시작)" });
  });

  [events, deadlines, ongoing, starting].forEach(function(arr) {
    arr.sort(function(a, b) { return a.d - b.d; });
  });

  var sections = [
    ["📌 이번 주 일정", events.map(function(x) { return x.txt; })],
    ["🔥 이번 주 마감", deadlines.map(function(x) { return x.txt; })],
    ["▶ 진행 중", ongoing.map(function(x) { return x.txt; })],
    ["⏳ 이번 주 시작", starting.map(function(x) { return x.txt; })],
    ["✅ 지난주 완료", doneLast]
  ];

  var html = "", txt = "주간 브리핑 " + fmtMD(thisW[0]) + " ~ " + fmtMD(thisW[1]) + "\n";
  sections.forEach(function(sec) {
    html += "<h3>" + sec[0] + "</h3>";
    txt += "\n" + sec[0] + "\n";
    if (sec[1].length) {
      html += "<ul>" + sec[1].map(function(x) { return "<li>" + escHtml(x) + "</li>"; }).join("") + "</ul>";
      txt += sec[1].map(function(x) { return "- " + x; }).join("\n") + "\n";
    } else {
      html += "<div class='brief-empty'>없음</div>";
      txt += "- 없음\n";
    }
  });

  document.getElementById("briefRange").textContent = fmtMD(thisW[0]) + " ~ " + fmtMD(thisW[1]);
  document.getElementById("briefBody").innerHTML = html;
  briefText = txt;
}
function showBriefing() {
  buildBriefing();
  document.getElementById("briefOverlay").classList.add("show");
}
function copyBriefing() {
  navigator.clipboard.writeText(briefText).then(function() { showToast("브리핑이 복사되었습니다"); });
}

// (서버는 백그라운드 상주 — 창을 닫아도 유지되어 다음 실행이 즉시 열림)
</script>
</body>
</html>
'''


def render_app():
    bs.ensure_data_file()
    with open(bs.DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = build_app_items(data)
    color_opts = [{"key": "", "bg": "#9ca3af"}]
    color_opts += [{"key": k, "bg": v.get("bg", "#9ca3af")} for k, v in data.get("colors", {}).items()]
    html = APP_HTML
    # "</script>" 포함 텍스트가 페이지를 깨뜨리지 않도록 이스케이프
    html = html.replace("{{GANTT_DATA}}", json.dumps(items, ensure_ascii=False).replace("</", "<\\/"))
    html = html.replace("{{COLOR_OPTS}}", json.dumps(color_opts, ensure_ascii=False))
    html = html.replace("{{COLOR_CSS}}", bs.build_color_css(data.get("colors", {})))
    html = html.replace("{{LAST_UPDATED}}", data.get("last_updated", ""))
    html = html.replace("{{HOLIDAYS}}", json.dumps(bs.load_holidays(), ensure_ascii=False))
    html = html.replace("{{DATA_TOKEN}}", data_token())
    html = html.replace("{{APP_VERSION}}", APP_VERSION)
    # 로컬 자산이 없으면(공개 저장소에서 갓 받은 경우 등) CDN으로 폴백
    if not all(os.path.isfile(os.path.join(bs.BASE_DIR, "assets", n)) for n in ASSETS):
        html = html.replace(f"/assets/dhtmlxgantt.js?v={APP_VERSION}",
                            "https://cdn.dhtmlx.com/gantt/edge/dhtmlxgantt.js")
        html = html.replace(f"/assets/dhtmlxgantt.css?v={APP_VERSION}",
                            "https://cdn.dhtmlx.com/gantt/edge/dhtmlxgantt.css")
    return html


# ══════════════════════ HTTP 서버 ══════════════════════

class ExclusiveHTTPServer(ThreadingHTTPServer):
    """Windows의 SO_REUSEADDR는 이미 사용 중인 포트에도 바인드를 허용해
    두 서버가 같은 포트를 공유하는 사고가 난다 — 배타적 바인드로 차단."""
    allow_reuse_address = False

SAVE_LOCK = threading.Lock()
# 코드가 수정되면 상주 서버를 자동 교체하기 위한 버전 표식
# (mtime이 아닌 내용 해시 — 동기화로 mtime만 바뀌어도 불필요한 재시작 없음)
with open(os.path.abspath(__file__), "rb") as _f:
    APP_VERSION = hashlib.md5(_f.read()).hexdigest()[:12]


def data_token():
    """schedule_data.json의 세대 표식 (오래된 창의 저장이 최신 데이터를 덮지 않도록)"""
    try:
        return str(os.stat(bs.DATA_FILE).st_mtime_ns)
    except OSError:
        return "0"
ASSETS = {
    "dhtmlxgantt.js": "application/javascript; charset=utf-8",
    "dhtmlxgantt.css": "text/css; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, body, content_type="text/html; charset=utf-8", code=200):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html", "/edit", "/edit/"):
            try:
                self._send(render_app())
            except Exception as e:
                import traceback
                traceback.print_exc()
                self._send(f"앱 로드 실패: {e}", code=500)
        elif self.path == "/ping":
            self.send_response(204)
            self.send_header("X-App-Version", APP_VERSION)
            self.end_headers()
        elif self.path.startswith("/assets/"):
            name = os.path.basename(self.path.split("?")[0])
            ctype = ASSETS.get(name)
            path = os.path.join(bs.BASE_DIR, "assets", name)
            if ctype and os.path.isfile(path):
                with open(path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/save":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if isinstance(payload, dict) and "tasks" in payload:
                flat = payload["tasks"]
                new_colors = payload.get("colors") or {}
                # 낙관적 잠금: 페이지가 로드된 이후 데이터가 다른 곳에서 바뀌었으면 거부
                token = payload.get("data_token")
                if token and token != data_token():
                    raise ValueError(
                        "이 화면을 연 뒤에 다른 창(또는 다른 도구)에서 데이터가 변경되었습니다. "
                        "새로고침(F5) 후 다시 편집해주세요. (지금 저장하면 그쪽 변경이 사라집니다)")
                cli_ver = payload.get("app_version")
                if cli_ver and cli_ver != APP_VERSION:
                    raise ValueError("앱이 업데이트되었습니다. 새로고침(F5) 후 다시 저장해주세요.")
            else:  # 구형 페이로드 (배열)
                flat = payload
                new_colors = {}

            warn = ""
            with SAVE_LOCK:  # 동시 저장 직렬화
                with open(bs.DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                merge_new_colors(data, new_colors)
                data = save_from_flat(flat, data)

                errors = validate_schedule.validate(data)
                if errors:
                    raise ValueError("데이터 검증 실패: " + "; ".join(errors))

                backup_data()
                # 원자적 쓰기: 임시 파일에 완성 후 교체 (쓰기 중단·동시 읽기에도 파손 없음)
                tmp = bs.DATA_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, bs.DATA_FILE)
                try:
                    bs.main()  # NAS 공유용 업무_스케줄.html 재빌드
                except Exception as e2:
                    warn = f"저장은 완료됐지만 공유용 HTML 재빌드에 실패했습니다: {e2}"

            print(f"[{datetime.now().strftime('%H:%M:%S')}] 저장 완료" + (" (재빌드 실패)" if warn else " + 재빌드 완료"))
            self._send(json.dumps({"ok": True, "warn": warn}, ensure_ascii=False),
                       "application/json; charset=utf-8")
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False),
                       "application/json; charset=utf-8", 500)


def _maximize_app_window(timeout=15.0):
    """앱 창을 찾아 강제 최대화.
    (Chromium --app 창은 --start-maximized를 무시하고 마지막 크기를 복원하기 때문)"""
    import ctypes
    user32 = ctypes.windll.user32
    SW_MAXIMIZE = 3
    target = "업무 스케줄"
    proc_t = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = []

        def cb(hwnd, lparam):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                # 앱 창 제목은 페이지 제목 그대로 (브라우저 탭 창은 ' - Edge' 등이 붙음)
                if buf.value.strip() == target and user32.IsWindowVisible(hwnd):
                    found.append(hwnd)
            return True

        user32.EnumWindows(proc_t(cb), 0)
        if found:
            for h in found:
                user32.ShowWindow(h, SW_MAXIMIZE)
            return True
        time.sleep(0.5)
    return False


def open_app_window(url):
    """주소창 없는 독립 앱 창으로 열기 (Edge/Chrome --app 모드, 전체화면)"""
    candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for exe in candidates:
        if os.path.isfile(exe):
            try:
                import ctypes
                sw = ctypes.windll.user32.GetSystemMetrics(0)
                sh = ctypes.windll.user32.GetSystemMetrics(1)
                size_args = [f"--window-size={sw},{sh}", "--window-position=0,0"]
            except Exception:
                size_args = []
            subprocess.Popen([exe, f"--app={url}", "--start-maximized"] + size_args)
            threading.Thread(target=_maximize_app_window, daemon=True).start()
            return
    webbrowser.open(url)  # 폴백: 일반 브라우저


def listening_ports(candidates):
    """후보 중 실제로 리스닝 중인 포트만 골라냄.
    (이 PC 방화벽은 빈 포트 접속을 즉시 거부하지 않고 타임아웃까지 끌어서,
    빈 포트에 HTTP 프로브를 하면 포트당 1.5초씩 낭비됨 — netstat으로 선별)"""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=NOWIN).stdout
    except Exception:
        return set(candidates)  # 판별 불가 시 전체 확인 (느리지만 안전)
    found = set()
    cand = {str(c) for c in candidates}
    for line in out.splitlines():
        if "LISTENING" not in line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        port = parts[1].rsplit(":", 1)[-1]
        if port in cand:
            found.add(int(port))
    return found


def probe_port(port):
    """포트 상태 판별: 'free' / 'ours-live'(같은 버전) / 'ours-old'(구버전 앱) / 'other'(무관 서버)"""
    import urllib.error
    import urllib.request
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=1.5)
        if r.status == 204:
            ver = r.headers.get("X-App-Version")
            if ver == APP_VERSION:
                return "ours-live"
            if ver:  # 우리 앱이지만 코드가 바뀜 → 교체 대상
                return "ours-old"
            return "other"  # 버전 표식 없는 204 → 무관 서버일 수 있으니 보호
    except urllib.error.HTTPError:
        pass  # 404 등 → 서버는 있으나 /ping 미지원 (구세대 또는 무관)
    except Exception:
        return "free"  # 연결 거부/타임아웃
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1.5)
        body = r.read(30000).decode("utf-8", "ignore")
        if "업무 스케줄" in body or "스케줄 편집" in body:
            return "ours-old"
    except Exception:
        pass
    return "other"


def kill_python_on_port(port):
    """해당 포트를 점유한 파이썬 프로세스만 종료 (우리 구세대 서버로 확인된 경우에만 호출).
    종료 성공 여부를 확인하고, 포트가 실제로 해제될 때까지 잠시 대기한다."""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=NOWIN).stdout
    except Exception:
        return
    my_pid = str(os.getpid())
    killed_any = False
    for line in out.splitlines():
        if "LISTENING" not in line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local, pid = parts[1], parts[-1]
        if local.endswith(f":{port}") and pid.isdigit() and pid != my_pid:
            try:
                info = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                                      capture_output=True, text=True, timeout=10,
                                      creationflags=NOWIN).stdout.lower()
                if "python" in info:
                    r = subprocess.run(["taskkill", "/PID", pid, "/F"],
                                       capture_output=True, timeout=10, creationflags=NOWIN)
                    if r.returncode == 0:
                        killed_any = True
                        print(f"이전 서버(PID {pid}, 포트 {port})를 정리했습니다.")
                    else:
                        print(f"! 이전 서버(PID {pid}) 종료 실패 (코드 {r.returncode})")
            except Exception as e:
                print(f"! 이전 서버 정리 중 오류: {e}")
    if killed_any:
        for _ in range(10):  # 포트 해제 대기 (최대 3초)
            if port not in listening_ports([port]):
                break
            time.sleep(0.3)


def main():
    bs.ensure_data_file()
    # 포트 정리: 현세대 앱이 이미 떠 있으면 창만 열고 종료(단일 인스턴스),
    # 구세대 앱이면 정리, 무관한 서버면 건드리지 않고 다음 포트 사용.
    # 실제 리스닝 중인 포트만 확인해 빈 포트 타임아웃 낭비를 없앤다.
    candidates = list(range(PORT, PORT + 10))
    for p in sorted(listening_ports(candidates)):
        state = probe_port(p)
        if state == "ours-live":
            url = f"http://127.0.0.1:{p}/"
            print(f"이미 실행 중인 앱을 발견했습니다 — 창만 엽니다: {url}")
            open_app_window(url)
            return
        if state == "ours-old":
            kill_python_on_port(p)

    port = PORT
    server = None
    for _ in range(10):
        try:
            server = ExclusiveHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            port += 1
    if server is None:
        print("사용 가능한 포트를 찾지 못했습니다.")
        return

    url = f"http://127.0.0.1:{port}/"
    print("=" * 50)
    print("  업무 스케줄 앱")
    print(f"  주소: {url}")
    print("  서버는 백그라운드 상주합니다 (다음 실행은 창만 열어 즉시).")
    print("=" * 50)

    def _holiday_refresh():
        try:
            bs.refresh_holidays()
        except Exception as e:
            print(f"공휴일 자동 갱신 실패 (캐시/내장 데이터로 동작): {e}")

    threading.Thread(target=_holiday_refresh, daemon=True).start()  # 공휴일 자동 갱신
    threading.Timer(0.2, open_app_window, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("종료합니다.")


if __name__ == "__main__":
    main()
