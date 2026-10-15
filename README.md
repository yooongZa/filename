# filename

사진과 영상을 로컬 AI 모델로 분석하고, 원래 파일명 뒤에 태그 5개를 붙이는 Windows·Mac 프로그램입니다.

**폴더 선택 → 분석 → 파일명 검토 → 변경**

```text
IMG_001.jpg → IMG_001__해변_자전거_도로_라이딩_야외.jpg
```

## 다운로드

| 운영체제 | 실행 파일 | 실행 방법 |
|---|---|---|
| Mac · Apple Silicon | [filename-macos-arm64.zip](https://github.com/yooongZa/filename/releases/download/v0.1.0/filename-macos-arm64.zip) | 압축 해제 후 `filename.app` 실행 |
| Windows · x64 | [filename-windows-x64.zip](https://github.com/yooongZa/filename/releases/download/v0.1.0/filename-windows-x64.zip) | 전체 압축 해제 후 `filename/filename.exe` 실행. `_internal` 폴더도 함께 보관 |

[배포 내역과 체크섬](https://github.com/yooongZa/filename/releases/tag/v0.1.0)

v0.1.0은 개발용 배포본입니다. Mac 앱은 Apple 공증을 받지 않았고, Windows 실행 파일은 배포용 코드 서명이 없습니다. 운영체제의 실행 확인 절차가 나타날 수 있습니다. Intel Mac 실행 파일은 이번 배포에 포함하지 않습니다.

## 사용 방법

1. 프로그램을 실행하면 브라우저에 로컬 화면이 열립니다.
2. 사진·영상이 있는 폴더를 선택하고 **분석**을 누릅니다.
3. 제안된 태그와 파일명을 확인하고 필요하면 수정합니다.
4. **검토 완료 · 변경**을 누릅니다. 앱 종료는 화면의 **종료** 버튼을 사용합니다.

기본값은 **변경 전 검토 켜짐 / 자동 변경 꺼짐**입니다. 두 옵션은 화면에서 바꿀 수 있으며, 검토를 끄고 자동 변경을 켜면 유효한 태그 5개 결과를 자동 반영합니다. 검토가 켜져 있으면 검토 버튼을 눌러야 변경됩니다.

처리 이력에 있는 파일과 같은 5태그 규칙으로 이름이 붙은 파일은 다음 분석에서 건너뜁니다. 이름이 충돌하면 덮어쓰지 않습니다. 선택한 폴더의 `.filename/`에 변경 이력을 저장하며 **변경 이력 되돌리기**로 복원할 수 있습니다.

사진은 JPG·JPEG·PNG·WEBP·BMP, 영상은 MP4·MOV·M4V·MKV·WEBM·AVI를 지원합니다. 영상 프레임은 프로그램이 추출합니다. HEIC·RAW는 현재 지원하지 않습니다.

## 모델 준비

모델은 **Gemma 4 12B QAT · Q4_0**입니다. 지정한 경로와 LM Studio 기본 모델 폴더 등에서 모델·프로젝터를 찾고, 없으면 분석 시작 시 [Google 공식 GGUF](https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf)를 내려받습니다. LM Studio를 실행할 필요는 없습니다.

모델 파일은 ZIP에 포함하지 않습니다. 처음 내려받을 때 인터넷과 약 7.2 GB 이상의 모델 저장 공간이 필요합니다. 실행 엔진도 없으면 준비합니다. 설치 후 이미지·영상 분석은 로컬에서 실행됩니다. Windows 8GB VRAM에서의 실제 추론과 적정 입력량은 추가 확인이 필요합니다.

## 설정과 프롬프트

첫 실행 때 다음 사용자 폴더에 편집할 설정을 복사합니다.

- Mac: `~/Library/Application Support/filename/`
- Windows: `%LOCALAPPDATA%/filename/`

| 파일 | 바꿀 수 있는 내용 |
|---|---|
| `configs/app.json` | 검토·자동 변경, 하위 폴더, 모델·엔진 경로 |
| `configs/profiles/gemma4_12b.json` | 이미지 해상도, 프레임 수, 토큰 예산, 문맥 길이, 전처리 |
| `assets/prompt_v3_five_tags.txt` | 매 분석마다 읽는 v3 기반 프롬프트 |
| `assets/schema_v3_five_tags.json` | 태그 5개 등 JSON 출력 규칙 |

화면의 **설정 파일** 버튼으로 설정을 열 수 있습니다. 자세한 내용은 [앱 사용 안내](docs/APP.md)와 [설정 안내](docs/CONFIG.md)를 참고하세요.

## 코드로 실행

Python 3.12 환경에서 실행합니다.

```bash
git clone https://github.com/yooongZa/filename.git
cd filename
python -m venv .venv
# Mac: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-app.txt
python app.py
```

`python app.py --mock`은 모델 없이 화면을 확인하는 모의 실행이며 실제 파일명을 변경하지 않습니다.

```text
app.py                  프로그램 시작
filename_app/           화면·분석·모델 준비·이름 변경
filename_bench/         기존 실험 코드에서 재사용한 전처리·모델 연결 도구
configs/                앱 설정과 추론 조건
assets/                 프롬프트와 JSON 출력 규칙
scripts/                실행 파일 빌드와 패키징 검사
tests/                  임시 파일로 실행하는 테스트
```

## 테스트와 빌드

```bash
python -m unittest discover -s tests -v
python -m pip install "pyinstaller>=6,<7"
python scripts/build_desktop.py
```

Mac에서 빌드하면 `dist/filename.app`, Windows에서 빌드하면 `dist/filename/filename.exe`가 생성됩니다. 각 운영체제에서 빌드해야 합니다. Windows 자동 빌드는 [GitHub Actions](https://github.com/yooongZa/filename/actions/workflows/desktop.yml)에서 실행합니다.

패키징 검사:

```bash
# Mac
python scripts/smoke_desktop.py dist/filename.app/Contents/MacOS/filename
# Windows
python scripts/smoke_desktop.py dist/filename/filename.exe
```

v0.1.0은 Mac 실제 모델의 사진·영상 분석, 임시 복사본 이름 변경·건너뛰기·복원, Mac·Windows 패키징 앱의 사진·영상 모의 분석을 확인했습니다. Windows 실제 GPU 추론은 아직 검증하지 않았습니다. 빌드 출처와 파일 해시는 [BUILD_INFO.json](docs/BUILD_INFO.json)에 기록했습니다.

관련 실험 저장소: [모델 품질·Colab](https://github.com/yooongZa/filename-model-quality-bench), [로컬 실행 성능](https://github.com/yooongZa/filename-local-capacity-bench).

Based-On: none — 기존 filename 앱과 공통 도구를 별도 저장소로 옮겼습니다. LMS 자료와 개인 촬영 데이터는 포함하지 않습니다.
