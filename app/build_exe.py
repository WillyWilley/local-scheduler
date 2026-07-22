# -*- coding: utf-8 -*-
"""
exe 배포본 빌드 (파이썬 없는 PC용)

개발용 도구다 — 빌드하는 PC에만 PyInstaller가 필요하고,
만들어진 앱의 런타임은 여전히 표준 라이브러리만 쓴다.

사용법 (프로젝트 루트에서):
  python app\\build_exe.py

결과:
  output/exe/업무스케줄/          ← 통째로 복사해서 쓰는 배포 폴더
  output/업무스케줄_배포.zip      ← 지인에게 전달할 압축본
"""
import os
import shutil
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))   # app/
BASE_DIR = os.path.dirname(SCRIPT_DIR)                     # 프로젝트 루트
DIST_DIR = os.path.join(BASE_DIR, "output", "exe")
WORK_DIR = os.path.join(BASE_DIR, "output", "exe_build")
APP_NAME = "업무스케줄"

README = """업무 스케줄 앱 (exe 배포본)
================================

실행: 업무스케줄.exe 더블클릭
  - 파이썬 설치가 필요 없습니다. Edge(윈도우 기본 브라우저)가 있으면 앱 창으로 열립니다.
  - 일정 데이터는 이 폴더의 data\\schedule_data.json에 저장됩니다.
  - 처음 실행하면 예시 일정으로 시작합니다. 자유롭게 지우고 쓰세요.

처음 실행 시 "Windows의 PC 보호" 파란 창이 뜨면:
  "추가 정보" → "실행"을 한 번만 눌러주면 됩니다.
  (개인 제작 프로그램이라 서명이 없어서 뜨는 안내입니다)

폴더를 옮겨도 됩니다. 단, 폴더 안의 파일 구성(assets, data)은 그대로 유지하세요.
"""


def run_pyinstaller():
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--noconsole",
        "--name", APP_NAME,
        "--distpath", DIST_DIR,
        "--workpath", WORK_DIR,
        "--specpath", WORK_DIR,
        os.path.join(SCRIPT_DIR, "schedule_web_editor.py"),
    ]
    print("PyInstaller 실행:", " ".join(cmd))
    subprocess.run(cmd, check=True)


def assemble():
    app_dir = os.path.join(DIST_DIR, APP_NAME)
    # 자산·예시 데이터를 exe 옆에 배치 (frozen 모드는 exe 옆 경로를 쓴다)
    shutil.copytree(os.path.join(BASE_DIR, "assets"),
                    os.path.join(app_dir, "assets"), dirs_exist_ok=True)
    os.makedirs(os.path.join(app_dir, "data"), exist_ok=True)
    shutil.copyfile(os.path.join(BASE_DIR, "data", "schedule_data.sample.json"),
                    os.path.join(app_dir, "data", "schedule_data.sample.json"))
    with open(os.path.join(app_dir, "읽어주세요.txt"), "w", encoding="utf-8-sig") as f:
        f.write(README)
    return app_dir


def make_zip(app_dir):
    zip_base = os.path.join(BASE_DIR, "output", APP_NAME + "_배포")
    if os.path.exists(zip_base + ".zip"):
        os.remove(zip_base + ".zip")
    shutil.make_archive(zip_base, "zip", os.path.dirname(app_dir), APP_NAME)
    return zip_base + ".zip"


def main():
    run_pyinstaller()
    app_dir = assemble()
    zip_path = make_zip(app_dir)
    exe = os.path.join(app_dir, APP_NAME + ".exe")
    print("=" * 50)
    print("빌드 완료")
    print("  배포 폴더:", app_dir)
    print("  압축본:  ", zip_path)
    print("  exe 크기: %.1f MB" % (os.path.getsize(exe) / 1e6))
    print("=" * 50)


if __name__ == "__main__":
    main()
