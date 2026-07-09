# -*- coding: utf-8 -*-
"""
GitHub 공개용 내보내기

개인 데이터(실제 일정, 백업, 로그, 빌드 산출물)를 제외한 공개 가능한 파일만
새 폴더로 복사한다. 이 작업 폴더의 git 히스토리에는 실제 업무 일정이 들어
있으므로, 공개는 반드시 이 내보내기 결과물로 "새 저장소"를 만들어야 한다.

사용법 (프로젝트 루트에서):
  python app\\export_github.py                → 기본 위치(옆 폴더)로 내보내기
  python app\\export_github.py D:\\내보낼\\경로  → 지정 위치로 내보내기

이후:
  cd <내보낸 폴더>
  git init && git add -A && git commit -m "첫 공개"
  (GitHub에 새 저장소를 만들고 push)
"""
import os
import shutil
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))   # app/
BASE_DIR = os.path.dirname(SCRIPT_DIR)                     # 프로젝트 루트

# 공개해도 되는 파일만 명시적으로 나열 (화이트리스트 방식, 루트 기준 상대경로)
PUBLIC_FILES = [
    "README.md",
    "LICENSE",
    "CLAUDE.md",
    "ROADMAP.md",
    "업무스케줄.bat",
    "app/build_schedule.py",
    "app/schedule_web_editor.py",
    "app/validate_schedule.py",
    "app/schedule_editor.py",
    "app/export_github.py",
    "data/schedule_data.sample.json",
    "docs/screenshot.png",
]

# 공개 저장소용 .gitignore (개인 데이터가 커밋되지 않도록)
PUBLIC_GITIGNORE = """__pycache__/
*.pyc
*.tmp
assets/
logs/
output/
data/backups/
data/schedule_data.json
data/holidays_cache.json
"""

# 절대 포함되면 안 되는 것들 (이중 안전장치 — 결과물 전체를 검사)
# 대상 폴더의 .git은 push용 저장소이므로 검사에서 제외 (내부는 살피지 않음)
FORBIDDEN_NAMES = {"schedule_data.json", "업무_스케줄.html", "구글캘린더_관리.md"}
FORBIDDEN_DIRS = {"backups", "logs", "output"}


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(BASE_DIR), "스케줄표_공개용")
    os.makedirs(target, exist_ok=True)

    copied = []
    for rel in PUBLIC_FILES:
        src = os.path.join(BASE_DIR, rel)
        if not os.path.isfile(src):
            print(f"  ! 건너뜀 (없음): {rel}")
            continue
        dst = os.path.join(target, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        copied.append(rel)

    with open(os.path.join(target, ".gitignore"), "w", encoding="utf-8") as f:
        f.write(PUBLIC_GITIGNORE)
    copied.append(".gitignore")

    # 이중 안전장치: 커밋될 수 있는 위치에 금지 항목이 섞였는지 검사.
    # (.git 내부와, 공개용 .gitignore가 이미 차단하는 로컬 실행 흔적
    #  — logs/, output/, data/schedule_data.json 등 — 은 제외)
    leaked = []
    ignored_dirs = FORBIDDEN_DIRS | {".git", "backups"}
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        rel_root = os.path.relpath(root, target)
        for fn in files:
            if fn == "schedule_data.json" and rel_root == "data":
                continue  # 로컬 실행용 (gitignore로 커밋 차단됨)
            if fn in FORBIDDEN_NAMES or fn == "app.log" or (fn.startswith("app-") and fn.endswith(".log")):
                leaked.append(os.path.join(root, fn))
    if leaked:
        print(f"!! 경고: 개인 데이터로 보이는 항목이 포함됨: {leaked}")
        sys.exit(1)

    print(f"내보내기 완료: {target}")
    print(f"  - 항목 {len(copied)}개: {', '.join(copied)}")
    print()
    print("다음 단계:")
    print(f"  cd \"{target}\"")
    print("  git init")
    print("  git add -A")
    print("  git commit -m \"첫 공개\"")
    print("  (GitHub에 새 저장소 생성 후 git remote add / git push)")


if __name__ == "__main__":
    main()
