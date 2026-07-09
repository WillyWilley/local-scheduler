# -*- coding: utf-8 -*-
"""
스케줄 JSON 편집기
schedule_data.json을 GUI로 편집하고, 빌드까지 한번에 실행

사용법: python schedule_editor.py
"""
import calendar
import json
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))   # app/
BASE_DIR = os.path.dirname(SCRIPT_DIR)                     # 프로젝트 루트
DATA_FILE = os.path.join(BASE_DIR, "data", "schedule_data.json")
BUILD_SCRIPT = os.path.join(SCRIPT_DIR, "build_schedule.py")

# ─── 색상 팔레트 ────────────────────────────────────────────
BG = "#f8f9fb"
CARD_BG = "#ffffff"
HEADER_BG = "#4f46e5"
HEADER_FG = "#ffffff"
ACCENT = "#4f46e5"
BORDER = "#e5e7eb"
TEXT_PRIMARY = "#1a1a2e"
TEXT_SECONDARY = "#6b7280"
BTN_BG = "#4f46e5"
BTN_FG = "#ffffff"
BTN_DANGER = "#dc2626"
BTN_SUCCESS = "#059669"


class ScheduleEditor:
    def __init__(self, root):
        self.root = root
        self.root.title("스케줄 편집기")
        self.root.geometry("1100x720")
        self.root.configure(bg=BG)
        self.root.minsize(900, 600)

        self.data = None
        self.unsaved = False
        self.current_node = None  # (type, path) of selected item
        self._current_apply_fn = None  # 현재 폼의 적용 함수

        self._load_data()
        self._build_ui()
        self._populate_tree()

    # ── 데이터 로드/저장 ──────────────────────────────────────
    def _load_data(self):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            self.data = json.load(f)

    def _save_and_build(self):
        """적용 → 저장 → 빌드 한번에 실행"""
        # 1) 현재 폼 변경사항 적용
        if self._current_apply_fn:
            self._current_apply_fn()

        # 2) JSON 저장
        self.data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        self.unsaved = False
        self._update_title()

        # 3) HTML 빌드
        try:
            result = subprocess.run(
                [sys.executable, BUILD_SCRIPT],
                capture_output=True, cwd=SCRIPT_DIR
            )
            if result.returncode == 0:
                messagebox.showinfo("완료", "저장 & 빌드 완료!")
            else:
                try:
                    err = result.stderr.decode("utf-8")
                except UnicodeDecodeError:
                    err = result.stderr.decode("cp949", errors="replace")
                messagebox.showerror("빌드 실패", f"JSON은 저장됨.\n빌드 오류:\n{err}")
        except Exception as e:
            messagebox.showerror("빌드 실패", f"JSON은 저장됨.\n오류: {e}")

    def _mark_dirty(self, *_):
        if not self.unsaved:
            self.unsaved = True
            self._update_title()

    def _update_title(self):
        mark = " *" if self.unsaved else ""
        self.root.title(f"스케줄 편집기{mark}")

    # ── UI 빌드 ──────────────────────────────────────────────
    def _build_ui(self):
        # 상단 헤더
        header = tk.Frame(self.root, bg=HEADER_BG, height=50)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="스케줄 편집기", font=("맑은 고딕", 14, "bold"),
                 bg=HEADER_BG, fg=HEADER_FG).pack(side=tk.LEFT, padx=16)

        btn_frame = tk.Frame(header, bg=HEADER_BG)
        btn_frame.pack(side=tk.RIGHT, padx=12)

        self._make_btn(btn_frame, "저장 & 빌드 (Ctrl+S)", self._save_and_build, BTN_SUCCESS).pack(side=tk.LEFT, padx=3)
        self._make_btn(btn_frame, "다시 불러오기", self._reload, TEXT_SECONDARY).pack(side=tk.LEFT, padx=3)

        self.root.bind("<Control-s>", lambda e: self._save_and_build())

        # 메인 영역: 트리 | 편집 폼
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # 왼쪽: 트리뷰 + 버튼
        left = tk.Frame(paned, bg=CARD_BG, bd=1, relief=tk.SOLID)
        paned.add(left, weight=2)

        tree_header = tk.Frame(left, bg=CARD_BG)
        tree_header.pack(fill=tk.X, padx=8, pady=(8, 4))
        tk.Label(tree_header, text="항목 목록", font=("맑은 고딕", 11, "bold"),
                 bg=CARD_BG, fg=TEXT_PRIMARY).pack(side=tk.LEFT)

        tree_btns = tk.Frame(tree_header, bg=CARD_BG)
        tree_btns.pack(side=tk.RIGHT)
        self._make_small_btn(tree_btns, "+ 추가", self._add_item).pack(side=tk.LEFT, padx=2)
        self._make_small_btn(tree_btns, "삭제", self._delete_item, BTN_DANGER).pack(side=tk.LEFT, padx=2)
        self._make_small_btn(tree_btns, "▲", self._move_up).pack(side=tk.LEFT, padx=2)
        self._make_small_btn(tree_btns, "▼", self._move_down).pack(side=tk.LEFT, padx=2)

        tree_frame = tk.Frame(left, bg=CARD_BG)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self.tree = ttk.Treeview(tree_frame, selectmode="browse", show="tree")
        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # 오른쪽: 편집 폼
        right = tk.Frame(paned, bg=CARD_BG, bd=1, relief=tk.SOLID)
        paned.add(right, weight=3)

        form_header = tk.Frame(right, bg=CARD_BG)
        form_header.pack(fill=tk.X, padx=12, pady=(10, 4))
        self.form_title = tk.Label(form_header, text="항목을 선택하세요",
                                   font=("맑은 고딕", 11, "bold"),
                                   bg=CARD_BG, fg=TEXT_PRIMARY)
        self.form_title.pack(side=tk.LEFT)

        self.form_area = tk.Frame(right, bg=CARD_BG)
        self.form_area.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 10))

        # 상태바
        status = tk.Frame(self.root, bg=BORDER, height=24)
        status.pack(fill=tk.X, side=tk.BOTTOM)
        status.pack_propagate(False)
        self.status_label = tk.Label(status, text=f"파일: {DATA_FILE}", font=("맑은 고딕", 9),
                                     bg=BORDER, fg=TEXT_SECONDARY, anchor=tk.W)
        self.status_label.pack(fill=tk.X, padx=8)

    def _make_btn(self, parent, text, cmd, color=BTN_BG):
        btn = tk.Button(parent, text=text, command=cmd, font=("맑은 고딕", 9),
                        bg=color, fg="#fff", activebackground=color, activeforeground="#fff",
                        bd=0, padx=12, pady=4, cursor="hand2")
        return btn

    def _make_small_btn(self, parent, text, cmd, color=ACCENT):
        btn = tk.Button(parent, text=text, command=cmd, font=("맑은 고딕", 9),
                        bg=CARD_BG, fg=color, activeforeground=color,
                        bd=1, relief=tk.SOLID, padx=6, pady=1, cursor="hand2")
        return btn

    # ── 트리 채우기 ──────────────────────────────────────────
    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        self._node_map = {}  # tree_id -> (type, index_path)

        for si, section in enumerate(self.data.get("sections", [])):
            sec_id = self.tree.insert("", tk.END, text=section["title"], open=True)
            self._node_map[sec_id] = ("section", [si])

            if section["type"] == "project":
                for pi, proj in enumerate(section.get("projects", [])):
                    proj_label = f"[{proj.get('color','')}] {proj['title']}"
                    if proj.get("start"):
                        proj_label += f"  ({proj['start']}, {proj.get('duration','')}일)"
                    proj_id = self.tree.insert(sec_id, tk.END, text=proj_label, open=True)
                    self._node_map[proj_id] = ("project", [si, pi])

                    for spi, sub in enumerate(proj.get("sub_projects", [])):
                        sub_label = sub["title"]
                        if sub.get("start"):
                            sub_label += f"  ({sub['start']}, {sub.get('duration','')}일)"
                        sub_id = self.tree.insert(proj_id, tk.END, text=sub_label, open=True)
                        self._node_map[sub_id] = ("sub_project", [si, pi, spi])

                        for ti, task in enumerate(sub.get("tasks", [])):
                            tag = "undetermined" if task.get("status") == "undetermined" else ""
                            label = task["title"]
                            if task.get("start"):
                                label += f"  ({task['start']}, {task['duration']}일)"
                            t_id = self.tree.insert(sub_id, tk.END, text=label, tags=(tag,))
                            self._node_map[t_id] = ("task", [si, pi, spi, ti])

            elif section["type"] == "event":
                for ei, evt in enumerate(section.get("events", [])):
                    label = f"{evt['title']}  ({evt['start']}"
                    if evt.get("time"):
                        label += f" {evt['time']}"
                    label += ")"
                    e_id = self.tree.insert(sec_id, tk.END, text=label)
                    self._node_map[e_id] = ("event", [si, ei])

        self.tree.tag_configure("undetermined", foreground="#92400e")

    def _get_item_by_path(self, item_type, path):
        """path 인덱스 배열로 실제 데이터 객체 반환"""
        if item_type == "section":
            return self.data["sections"][path[0]]
        elif item_type == "project":
            return self.data["sections"][path[0]]["projects"][path[1]]
        elif item_type == "sub_project":
            return self.data["sections"][path[0]]["projects"][path[1]]["sub_projects"][path[2]]
        elif item_type == "task":
            return self.data["sections"][path[0]]["projects"][path[1]]["sub_projects"][path[2]]["tasks"][path[3]]
        elif item_type == "event":
            return self.data["sections"][path[0]]["events"][path[1]]
        return None

    # ── 선택 시 폼 표시 ──────────────────────────────────────
    def _on_select(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        node_id = sel[0]
        if node_id not in self._node_map:
            return

        item_type, path = self._node_map[node_id]
        self.current_node = (item_type, path, node_id)
        item = self._get_item_by_path(item_type, path)

        # 폼 초기화
        self._current_apply_fn = None
        for w in self.form_area.winfo_children():
            w.destroy()

        type_labels = {
            "section": "섹션", "project": "프로젝트",
            "sub_project": "세부 프로젝트", "task": "업무",
            "event": "일정"
        }
        self.form_title.config(text=f"{type_labels.get(item_type, '')} 편집")

        if item_type == "section":
            self._build_section_form(item)
        elif item_type == "project":
            self._build_project_form(item)
        elif item_type == "sub_project":
            self._build_sub_project_form(item)
        elif item_type == "task":
            self._build_task_form(item)
        elif item_type == "event":
            self._build_event_form(item)

    def _add_form_row(self, parent, label, row, widget_factory):
        tk.Label(parent, text=label, font=("맑은 고딕", 10), bg=CARD_BG,
                 fg=TEXT_SECONDARY, anchor=tk.W).grid(row=row, column=0, sticky=tk.W, pady=(6, 2))
        widget = widget_factory(parent)
        widget.grid(row=row, column=1, sticky=tk.EW, pady=(6, 2), padx=(8, 0))
        return widget

    def _make_entry(self, parent, var=None):
        e = tk.Entry(parent, font=("맑은 고딕", 10), relief=tk.SOLID, bd=1)
        if var is not None and var != "":
            e.insert(0, str(var))
        return e

    def _add_date_row(self, parent, label, row, value):
        """날짜 입력칸 + 달력 버튼이 있는 폼 행"""
        tk.Label(parent, text=label, font=("맑은 고딕", 10), bg=CARD_BG,
                 fg=TEXT_SECONDARY, anchor=tk.W).grid(row=row, column=0, sticky=tk.W, pady=(6, 2))
        wrap = tk.Frame(parent, bg=CARD_BG)
        wrap.grid(row=row, column=1, sticky=tk.EW, pady=(6, 2), padx=(8, 0))
        e = tk.Entry(wrap, font=("맑은 고딕", 10), relief=tk.SOLID, bd=1)
        if value not in (None, ""):
            e.insert(0, str(value))
        e.pack(side=tk.LEFT, fill=tk.X, expand=True)
        btn = tk.Button(wrap, text="📅", command=lambda: self._open_calendar(e),
                        font=("맑은 고딕", 9), bd=1, relief=tk.SOLID, bg=CARD_BG,
                        cursor="hand2", padx=4)
        btn.pack(side=tk.LEFT, padx=(4, 0))
        e._cal_btn = btn
        return e

    def _disable_schedule_fields(self, *widgets):
        """자식이 있는 항목: 일정은 자동 계산되므로 입력 비활성화"""
        for w in widgets:
            try:
                w.config(state="disabled")
                if isinstance(w, tk.Entry):
                    w.config(disabledbackground="#f3f4f6")
            except tk.TclError:
                pass
            btn = getattr(w, "_cal_btn", None)
            if btn:
                btn.config(state="disabled")

    def _open_calendar(self, entry):
        """entry 아래에 달력 팝업을 띄우고, 날짜 클릭 시 entry에 입력"""
        try:
            cur = datetime.strptime(entry.get().strip(), "%Y-%m-%d")
        except ValueError:
            cur = datetime.now()
        selected = cur.date()
        today = datetime.now().date()

        top = tk.Toplevel(self.root)
        top.title("날짜 선택")
        top.transient(self.root)
        top.resizable(False, False)
        top.configure(bg=CARD_BG)
        top.geometry(f"+{entry.winfo_rootx()}+{entry.winfo_rooty() + entry.winfo_height() + 2}")
        top.grab_set()
        top.bind("<Escape>", lambda e: top.destroy())

        state = {"year": cur.year, "month": cur.month}

        header = tk.Frame(top, bg=CARD_BG)
        header.pack(fill=tk.X, padx=8, pady=(6, 2))
        tk.Button(header, text="◀", command=lambda: move(-1), font=("맑은 고딕", 9),
                  bd=0, bg=CARD_BG, cursor="hand2").pack(side=tk.LEFT)
        title_lbl = tk.Label(header, font=("맑은 고딕", 10, "bold"), bg=CARD_BG, fg=TEXT_PRIMARY)
        title_lbl.pack(side=tk.LEFT, expand=True)
        tk.Button(header, text="▶", command=lambda: move(1), font=("맑은 고딕", 9),
                  bd=0, bg=CARD_BG, cursor="hand2").pack(side=tk.RIGHT)

        days_frame = tk.Frame(top, bg=CARD_BG)
        days_frame.pack(padx=8, pady=(0, 8))

        def pick(day):
            entry.delete(0, tk.END)
            entry.insert(0, f"{state['year']:04d}-{state['month']:02d}-{day:02d}")
            entry.event_generate("<KeyRelease>")
            top.destroy()

        def draw():
            for w in days_frame.winfo_children():
                w.destroy()
            title_lbl.config(text=f"{state['year']}년 {state['month']}월")
            weekday_colors = {5: "#2563eb", 6: "#dc2626"}  # 토, 일
            for i, wd in enumerate(["월", "화", "수", "목", "금", "토", "일"]):
                tk.Label(days_frame, text=wd, font=("맑은 고딕", 9), bg=CARD_BG,
                         fg=weekday_colors.get(i, TEXT_SECONDARY), width=3).grid(row=0, column=i)
            for r, week in enumerate(calendar.Calendar(firstweekday=0).monthdayscalendar(
                    state["year"], state["month"]), start=1):
                for c, day in enumerate(week):
                    if day == 0:
                        continue
                    d = datetime(state["year"], state["month"], day).date()
                    bg, fg = CARD_BG, TEXT_PRIMARY
                    if d == selected:
                        bg, fg = ACCENT, "#ffffff"
                    elif d == today:
                        bg = "#e0e7ff"
                    elif c >= 5:
                        fg = weekday_colors[c]
                    tk.Button(days_frame, text=str(day), width=3, bd=0, bg=bg, fg=fg,
                              font=("맑은 고딕", 9), cursor="hand2",
                              activebackground=ACCENT, activeforeground="#ffffff",
                              command=lambda d=day: pick(d)).grid(row=r, column=c, padx=1, pady=1)

        def move(delta):
            m = state["month"] + delta
            state["year"] += (m - 1) // 12
            state["month"] = (m - 1) % 12 + 1
            draw()

        draw()

    @staticmethod
    def _workday_end(start, dur):
        """시작일부터 주말(토·일) 제외 dur일째 되는 날 (시작일은 항상 1일째로 포함)"""
        d = start
        count = 1
        while count < dur:
            d += timedelta(days=1)
            if d.weekday() < 5:
                count += 1
        return d

    @staticmethod
    def _workday_count(start, end):
        """시작일~종료일(포함) 중 주말(토·일) 제외 일수 (시작일은 항상 포함)"""
        count = 1
        d = start
        while d < end:
            d += timedelta(days=1)
            if d.weekday() < 5:
                count += 1
        return count

    def _wire_duration_end_sync(self, e_start, e_dur, e_end):
        """기간 ↔ 종료일 양방향 자동 계산 (기간은 주말 제외 근무일, 종료일은 마지막 날 포함)"""
        syncing = {"on": False}

        v_start = tk.StringVar(value=e_start.get())
        v_dur = tk.StringVar(value=e_dur.get())
        v_end = tk.StringVar(value=e_end.get())
        e_start.config(textvariable=v_start)
        e_dur.config(textvariable=v_dur)
        e_end.config(textvariable=v_end)
        # StringVar가 GC로 사라지면 Tcl 변수도 삭제되므로 위젯에 참조를 붙잡아 둠
        e_start._sync_var = v_start
        e_dur._sync_var = v_dur
        e_end._sync_var = v_end

        def parse_date(s):
            try:
                return datetime.strptime(s.strip(), "%Y-%m-%d")
            except ValueError:
                return None

        def set_var(var, text):
            syncing["on"] = True
            var.set(text)
            syncing["on"] = False

        def update_end(*_):
            if syncing["on"]:
                return
            start = parse_date(v_start.get())
            try:
                dur = int(v_dur.get().strip())
            except ValueError:
                return
            if start and dur >= 1:
                end = self._workday_end(start, dur)
                set_var(v_end, end.strftime("%Y-%m-%d"))

        def update_dur(*_):
            if syncing["on"]:
                return
            start = parse_date(v_start.get())
            end = parse_date(v_end.get())
            if start and end and end >= start:
                set_var(v_dur, str(self._workday_count(start, end)))

        v_start.trace_add("write", update_end)
        v_dur.trace_add("write", update_end)
        v_end.trace_add("write", update_dur)
        update_end()

    def _build_section_form(self, item):
        frame = tk.Frame(self.form_area, bg=CARD_BG)
        frame.pack(fill=tk.X, pady=4)
        frame.columnconfigure(1, weight=1)

        e_title = self._add_form_row(frame, "제목", 0, lambda p: self._make_entry(p, item.get("title", "")))
        e_id = self._add_form_row(frame, "ID", 1, lambda p: self._make_entry(p, item.get("id", "")))

        type_var = tk.StringVar(value=item.get("type", "project"))
        self._add_form_row(frame, "유형", 2,
                           lambda p: ttk.Combobox(p, textvariable=type_var,
                                                   values=["project", "event"], state="readonly"))

        def apply():
            item["title"] = e_title.get()
            item["id"] = e_id.get()
            item["type"] = type_var.get()
            self._mark_dirty()
            self._populate_tree()

        self._add_apply_btn(apply)

    def _build_project_form(self, item):
        frame = tk.Frame(self.form_area, bg=CARD_BG)
        frame.pack(fill=tk.X, pady=4)
        frame.columnconfigure(1, weight=1)

        e_title = self._add_form_row(frame, "프로젝트명", 0, lambda p: self._make_entry(p, item.get("title", "")))
        e_id = self._add_form_row(frame, "ID", 1, lambda p: self._make_entry(p, item.get("id", "")))

        colors = list(self.data.get("colors", {}).keys())
        color_var = tk.StringVar(value=item.get("color", ""))
        self._add_form_row(frame, "색상", 2,
                           lambda p: ttk.Combobox(p, textvariable=color_var, values=colors))

        # 일정 필드: 자식(세부 프로젝트)이 있으면 자동 계산되므로 입력 불가
        has_children = bool(item.get("sub_projects"))
        sched_label = ("── 일정: 하위 항목에서 자동 계산됨 (입력 불가) ──" if has_children
                       else "── 일정 (자식이 없는 프로젝트만 직접 입력) ──")
        tk.Label(self.form_area, text=sched_label,
                 font=("맑은 고딕", 9), bg=CARD_BG, fg=TEXT_SECONDARY).pack(fill=tk.X, pady=(10, 0))

        frame2 = tk.Frame(self.form_area, bg=CARD_BG)
        frame2.pack(fill=tk.X, pady=4)
        frame2.columnconfigure(1, weight=1)

        e_start = self._add_date_row(frame2, "시작일 (YYYY-MM-DD)", 0, item.get("start", ""))
        e_dur = self._add_form_row(frame2, "기간 (일)", 1, lambda p: self._make_entry(p, item.get("duration", "")))
        e_end = self._add_date_row(frame2, "종료일 (YYYY-MM-DD)", 2, "")
        e_prog = self._add_form_row(frame2, "진행률 (0~1)", 3, lambda p: self._make_entry(p, item.get("progress", "")))
        self._wire_duration_end_sync(e_start, e_dur, e_end)

        status_var = tk.StringVar(value=item.get("status", ""))
        cb_status = self._add_form_row(frame2, "상태", 4,
                                       lambda p: ttk.Combobox(p, textvariable=status_var,
                                                              values=["", "undetermined"]))
        if has_children:
            self._disable_schedule_fields(e_start, e_dur, e_end, e_prog, cb_status)

        tk.Label(self.form_area, text="메모", font=("맑은 고딕", 10), bg=CARD_BG,
                 fg=TEXT_SECONDARY, anchor=tk.W).pack(fill=tk.X, pady=(10, 2))
        notes_text = tk.Text(self.form_area, font=("맑은 고딕", 10), height=4, relief=tk.SOLID, bd=1, wrap=tk.WORD)
        notes_text.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        notes_text.insert("1.0", item.get("notes", ""))

        def apply():
            item["title"] = e_title.get()
            try:
                item["id"] = int(e_id.get())
            except ValueError:
                item["id"] = e_id.get()
            item["color"] = color_var.get()
            # 일정 필드 (자식이 있으면 자동 계산되므로 건드리지 않음)
            if not has_children:
                start_val = e_start.get().strip()
                dur_val = e_dur.get().strip()
                if start_val and dur_val:
                    item["start"] = start_val
                    try:
                        item["duration"] = int(dur_val)
                    except ValueError:
                        item["duration"] = 1
                    try:
                        prog_val = e_prog.get().strip()
                        item["progress"] = float(prog_val) if prog_val else 0
                    except ValueError:
                        item["progress"] = 0
                    st = status_var.get()
                    if st:
                        item["status"] = st
                    elif "status" in item:
                        del item["status"]
                else:
                    for key in ("start", "duration", "progress", "status"):
                        item.pop(key, None)
            notes = notes_text.get("1.0", tk.END).rstrip("\n")
            if notes:
                item["notes"] = notes
            elif "notes" in item:
                del item["notes"]
            self._mark_dirty()
            self._populate_tree()

        self._add_apply_btn(apply)

    def _build_sub_project_form(self, item):
        frame = tk.Frame(self.form_area, bg=CARD_BG)
        frame.pack(fill=tk.X, pady=4)
        frame.columnconfigure(1, weight=1)

        e_title = self._add_form_row(frame, "세부 프로젝트명", 0, lambda p: self._make_entry(p, item.get("title", "")))
        e_id = self._add_form_row(frame, "ID", 1, lambda p: self._make_entry(p, item.get("id", "")))

        # 일정 필드: 자식(업무)이 있으면 자동 계산되므로 입력 불가
        has_children = bool(item.get("tasks"))
        sched_label = ("── 일정: 하위 업무에서 자동 계산됨 (입력 불가) ──" if has_children
                       else "── 일정 (하위 업무가 없을 때만 직접 입력) ──")
        tk.Label(self.form_area, text=sched_label,
                 font=("맑은 고딕", 9), bg=CARD_BG, fg=TEXT_SECONDARY).pack(fill=tk.X, pady=(10, 0))

        frame2 = tk.Frame(self.form_area, bg=CARD_BG)
        frame2.pack(fill=tk.X, pady=4)
        frame2.columnconfigure(1, weight=1)

        e_start = self._add_date_row(frame2, "시작일 (YYYY-MM-DD)", 0, item.get("start", ""))
        e_dur = self._add_form_row(frame2, "기간 (일)", 1, lambda p: self._make_entry(p, item.get("duration", "")))
        e_end = self._add_date_row(frame2, "종료일 (YYYY-MM-DD)", 2, "")
        e_prog = self._add_form_row(frame2, "진행률 (0~1)", 3, lambda p: self._make_entry(p, item.get("progress", "")))
        self._wire_duration_end_sync(e_start, e_dur, e_end)

        status_var = tk.StringVar(value=item.get("status", ""))
        cb_status = self._add_form_row(frame2, "상태", 4,
                                       lambda p: ttk.Combobox(p, textvariable=status_var,
                                                              values=["", "undetermined"]))
        if has_children:
            self._disable_schedule_fields(e_start, e_dur, e_end, e_prog, cb_status)

        tk.Label(self.form_area, text="메모", font=("맑은 고딕", 10), bg=CARD_BG,
                 fg=TEXT_SECONDARY, anchor=tk.W).pack(fill=tk.X, pady=(10, 2))
        notes_text = tk.Text(self.form_area, font=("맑은 고딕", 10), height=4, relief=tk.SOLID, bd=1, wrap=tk.WORD)
        notes_text.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        notes_text.insert("1.0", item.get("notes", ""))

        def apply():
            item["title"] = e_title.get()
            try:
                item["id"] = int(e_id.get())
            except ValueError:
                item["id"] = e_id.get()
            # 일정 필드 (자식이 있으면 자동 계산되므로 건드리지 않음)
            if not has_children:
                start_val = e_start.get().strip()
                dur_val = e_dur.get().strip()
                if start_val and dur_val:
                    item["start"] = start_val
                    try:
                        item["duration"] = int(dur_val)
                    except ValueError:
                        item["duration"] = 1
                    try:
                        prog_val = e_prog.get().strip()
                        item["progress"] = float(prog_val) if prog_val else 0
                    except ValueError:
                        item["progress"] = 0
                    st = status_var.get()
                    if st:
                        item["status"] = st
                    elif "status" in item:
                        del item["status"]
                else:
                    for key in ("start", "duration", "progress", "status"):
                        item.pop(key, None)
            notes = notes_text.get("1.0", tk.END).rstrip("\n")
            if notes:
                item["notes"] = notes
            elif "notes" in item:
                del item["notes"]
            self._mark_dirty()
            self._populate_tree()

        self._add_apply_btn(apply)

    def _build_task_form(self, item):
        frame = tk.Frame(self.form_area, bg=CARD_BG)
        frame.pack(fill=tk.X, pady=4)
        frame.columnconfigure(1, weight=1)

        e_title = self._add_form_row(frame, "업무명", 0, lambda p: self._make_entry(p, item.get("title", "")))
        e_id = self._add_form_row(frame, "ID", 1, lambda p: self._make_entry(p, item.get("id", "")))
        e_start = self._add_date_row(frame, "시작일 (YYYY-MM-DD)", 2, item.get("start", ""))
        e_dur = self._add_form_row(frame, "기간 (일)", 3, lambda p: self._make_entry(p, item.get("duration", "")))
        e_end = self._add_date_row(frame, "종료일 (YYYY-MM-DD)", 4, "")
        e_prog = self._add_form_row(frame, "진행률 (0~1)", 5, lambda p: self._make_entry(p, item.get("progress", 0)))
        self._wire_duration_end_sync(e_start, e_dur, e_end)

        status_var = tk.StringVar(value=item.get("status", ""))
        self._add_form_row(frame, "상태", 6,
                           lambda p: ttk.Combobox(p, textvariable=status_var,
                                                   values=["", "undetermined"]))

        # 메모 (멀티라인)
        tk.Label(self.form_area, text="메모", font=("맑은 고딕", 10), bg=CARD_BG,
                 fg=TEXT_SECONDARY, anchor=tk.W).pack(fill=tk.X, pady=(10, 2))
        notes_text = tk.Text(self.form_area, font=("맑은 고딕", 10), height=6, relief=tk.SOLID, bd=1, wrap=tk.WORD)
        notes_text.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        notes_text.insert("1.0", item.get("notes", ""))

        def apply():
            item["title"] = e_title.get()
            try:
                item["id"] = int(e_id.get())
            except ValueError:
                item["id"] = e_id.get()
            item["start"] = e_start.get()
            try:
                item["duration"] = int(e_dur.get())
            except ValueError:
                item["duration"] = 1
            try:
                item["progress"] = float(e_prog.get())
            except ValueError:
                item["progress"] = 0
            st = status_var.get()
            if st:
                item["status"] = st
            elif "status" in item:
                del item["status"]
            item["notes"] = notes_text.get("1.0", tk.END).rstrip("\n")
            self._mark_dirty()
            self._populate_tree()

        self._add_apply_btn(apply)

    def _build_event_form(self, item):
        frame = tk.Frame(self.form_area, bg=CARD_BG)
        frame.pack(fill=tk.X, pady=4)
        frame.columnconfigure(1, weight=1)

        e_title = self._add_form_row(frame, "일정명", 0, lambda p: self._make_entry(p, item.get("title", "")))
        e_id = self._add_form_row(frame, "ID", 1, lambda p: self._make_entry(p, item.get("id", "")))
        e_start = self._add_date_row(frame, "날짜 (YYYY-MM-DD)", 2, item.get("start", ""))
        e_dur = self._add_form_row(frame, "기간 (일)", 3, lambda p: self._make_entry(p, item.get("duration", 1)))
        e_end = self._add_date_row(frame, "종료일 (YYYY-MM-DD)", 4, "")
        e_time = self._add_form_row(frame, "시간 (예: 10:00~11:30)", 5, lambda p: self._make_entry(p, item.get("time", "")))
        self._wire_duration_end_sync(e_start, e_dur, e_end)

        colors = list(self.data.get("colors", {}).keys())
        color_var = tk.StringVar(value=item.get("color", ""))
        self._add_form_row(frame, "색상", 6,
                           lambda p: ttk.Combobox(p, textvariable=color_var, values=colors))

        tk.Label(self.form_area, text="메모", font=("맑은 고딕", 10), bg=CARD_BG,
                 fg=TEXT_SECONDARY, anchor=tk.W).pack(fill=tk.X, pady=(10, 2))
        notes_text = tk.Text(self.form_area, font=("맑은 고딕", 10), height=4, relief=tk.SOLID, bd=1, wrap=tk.WORD)
        notes_text.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        notes_text.insert("1.0", item.get("notes", ""))

        def apply():
            item["title"] = e_title.get()
            try:
                item["id"] = int(e_id.get())
            except ValueError:
                item["id"] = e_id.get()
            item["start"] = e_start.get()
            try:
                item["duration"] = int(e_dur.get())
            except ValueError:
                item["duration"] = 1
            t = e_time.get().strip()
            if t:
                item["time"] = t
            elif "time" in item:
                del item["time"]
            c = color_var.get().strip()
            if c:
                item["color"] = c
            elif "color" in item:
                del item["color"]
            notes = notes_text.get("1.0", tk.END).rstrip("\n")
            if notes:
                item["notes"] = notes
            elif "notes" in item:
                del item["notes"]
            self._mark_dirty()
            self._populate_tree()

        self._add_apply_btn(apply)

    def _add_apply_btn(self, cmd):
        self._current_apply_fn = cmd

    # ── 추가/삭제/이동 ──────────────────────────────────────
    def _next_id(self, prefix=""):
        """기존 ID 중 최대값 + 1"""
        max_id = 0
        for section in self.data["sections"]:
            if section["type"] == "project":
                for proj in section.get("projects", []):
                    if isinstance(proj.get("id"), int):
                        max_id = max(max_id, proj["id"])
                    for sub in proj.get("sub_projects", []):
                        if isinstance(sub.get("id"), int):
                            max_id = max(max_id, sub["id"])
                        for task in sub.get("tasks", []):
                            if isinstance(task.get("id"), int):
                                max_id = max(max_id, task["id"])
            elif section["type"] == "event":
                for evt in section.get("events", []):
                    if isinstance(evt.get("id"), int):
                        max_id = max(max_id, evt["id"])
        return max_id + 1

    def _add_item(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("선택 필요", "추가할 위치의 상위 항목을 선택하세요.")
            return

        node_id = sel[0]
        if node_id not in self._node_map:
            return

        item_type, path = self._node_map[node_id]
        new_id = self._next_id()
        today = datetime.now().strftime("%Y-%m-%d")

        if item_type == "section":
            section = self._get_item_by_path("section", path)
            if section["type"] == "project":
                # 섹션 아래에 프로젝트 추가
                section.setdefault("projects", []).append({
                    "id": new_id,
                    "title": "새 프로젝트",
                    "color": "",
                    "sub_projects": []
                })
            elif section["type"] == "event":
                # 섹션 아래에 일정 추가
                section.setdefault("events", []).append({
                    "id": new_id,
                    "title": "새 일정",
                    "start": today,
                    "duration": 1,
                    "time": "",
                    "color": "",
                    "notes": ""
                })

        elif item_type == "project":
            proj = self._get_item_by_path("project", path)
            proj.setdefault("sub_projects", []).append({
                "id": new_id,
                "title": "새 세부 프로젝트",
                "tasks": []
            })

        elif item_type == "sub_project":
            sub = self._get_item_by_path("sub_project", path)
            sub.setdefault("tasks", []).append({
                "id": new_id,
                "title": "새 업무",
                "start": today,
                "duration": 5,
                "progress": 0,
                "notes": ""
            })

        elif item_type == "task":
            # 같은 레벨에 업무 추가 (부모 sub_project에)
            sub = self._get_item_by_path("sub_project", path[:3])
            sub["tasks"].append({
                "id": new_id,
                "title": "새 업무",
                "start": today,
                "duration": 5,
                "progress": 0,
                "notes": ""
            })

        elif item_type == "event":
            # 같은 레벨에 일정 추가 (부모 section에)
            section = self._get_item_by_path("section", path[:1])
            section["events"].append({
                "id": new_id,
                "title": "새 일정",
                "start": today,
                "duration": 1,
                "time": "",
                "color": "",
                "notes": ""
            })

        self._mark_dirty()
        self._populate_tree()

    def _delete_item(self):
        sel = self.tree.selection()
        if not sel:
            return

        node_id = sel[0]
        if node_id not in self._node_map:
            return

        item_type, path = self._node_map[node_id]
        item = self._get_item_by_path(item_type, path)
        name = item.get("title", item.get("text", ""))

        if not messagebox.askyesno("삭제 확인", f"'{name}'을(를) 삭제하시겠습니까?\n(하위 항목도 모두 삭제됩니다)"):
            return

        if item_type == "section":
            self.data["sections"].pop(path[0])
        elif item_type == "project":
            self.data["sections"][path[0]]["projects"].pop(path[1])
        elif item_type == "sub_project":
            self.data["sections"][path[0]]["projects"][path[1]]["sub_projects"].pop(path[2])
        elif item_type == "task":
            self.data["sections"][path[0]]["projects"][path[1]]["sub_projects"][path[2]]["tasks"].pop(path[3])
        elif item_type == "event":
            self.data["sections"][path[0]]["events"].pop(path[1])

        self._mark_dirty()
        self._populate_tree()
        # 폼 초기화
        self._current_apply_fn = None
        for w in self.form_area.winfo_children():
            w.destroy()
        self.form_title.config(text="항목을 선택하세요")

    def _get_parent_list_and_index(self, item_type, path):
        """이동용: 부모 리스트와 현재 인덱스 반환"""
        if item_type == "section":
            return self.data["sections"], path[0]
        elif item_type == "project":
            return self.data["sections"][path[0]]["projects"], path[1]
        elif item_type == "sub_project":
            return self.data["sections"][path[0]]["projects"][path[1]]["sub_projects"], path[2]
        elif item_type == "task":
            return self.data["sections"][path[0]]["projects"][path[1]]["sub_projects"][path[2]]["tasks"], path[3]
        elif item_type == "event":
            return self.data["sections"][path[0]]["events"], path[1]
        return None, None

    def _move_up(self):
        sel = self.tree.selection()
        if not sel or sel[0] not in self._node_map:
            return
        item_type, path = self._node_map[sel[0]]
        lst, idx = self._get_parent_list_and_index(item_type, path)
        if lst is None or idx <= 0:
            return
        lst[idx], lst[idx - 1] = lst[idx - 1], lst[idx]
        self._mark_dirty()
        self._populate_tree()

    def _move_down(self):
        sel = self.tree.selection()
        if not sel or sel[0] not in self._node_map:
            return
        item_type, path = self._node_map[sel[0]]
        lst, idx = self._get_parent_list_and_index(item_type, path)
        if lst is None or idx >= len(lst) - 1:
            return
        lst[idx], lst[idx + 1] = lst[idx + 1], lst[idx]
        self._mark_dirty()
        self._populate_tree()

    # ── 리로드 ────────────────────────────────────────────────
    def _reload(self):
        if self.unsaved:
            if not messagebox.askyesno("확인", "저장되지 않은 변경을 버리고 다시 불러오시겠습니까?"):
                return
        self._load_data()
        self.unsaved = False
        self._current_apply_fn = None
        self._update_title()
        self._populate_tree()
        for w in self.form_area.winfo_children():
            w.destroy()
        self.form_title.config(text="항목을 선택하세요")


def main():
    root = tk.Tk()
    root.iconname("스케줄 편집기")
    app = ScheduleEditor(root)
    root.mainloop()


if __name__ == "__main__":
    main()
