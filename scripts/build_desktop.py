"""각 OS에서 실행하여 해당 OS의 배포 파일을 만듭니다. 모델 가중치는 포함하지 않습니다."""

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
command = [
    sys.executable,
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name",
    "filename",
    "--collect-data",
    "imageio_ffmpeg",
    "--collect-data",
    "jsonschema_specifications",
    "--collect-data",
    "rfc3987_syntax",
    "--add-data",
    f'{ROOT / "filename_app/static"}{os.pathsep}filename_app/static',
    "--add-data",
    f'{ROOT / "configs/app.json"}{os.pathsep}configs',
    "--add-data",
    f'{ROOT / "configs/profiles/gemma4_12b.json"}{os.pathsep}configs/profiles',
    "--add-data",
    f'{ROOT / "assets/prompt_v3_five_tags.txt"}{os.pathsep}assets',
    "--add-data",
    f'{ROOT / "assets/schema_v3_five_tags.json"}{os.pathsep}assets',
]
# 앱은 llama-server를 사용합니다. Colab 전용 라이브러리는 배포판에 넣지 않습니다.
for module in [
    "torch",
    "torchvision",
    "transformers",
    "matplotlib",
    "pandas",
    "scipy",
    "IPython",
    "pytest",
    "tkinter",
]:
    command += ["--exclude-module", module]
command += [str(ROOT / "app.py")]
subprocess.run(command, cwd=ROOT, check=True)
print("Build complete:", ROOT / "dist")
