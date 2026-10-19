# filename

사진과 영상을 로컬 AI 모델로 분석하고, 원래 파일명 뒤에 태그 5개를 붙이는 Mac·Windows 프로그램입니다.

**폴더 선택 → 분석 → 태그 검토 → 파일명 변경**

```text
IMG_001.jpg → IMG_001__해변_자전거_도로_라이딩_야외.jpg
```

## 설치와 실행

- **Mac · Apple Silicon:** `filename.app`을 실행합니다.
- **Windows · x64:** ZIP을 모두 압축 해제한 뒤 `filename/filename.exe`를 실행합니다. `_internal` 폴더도 함께 보관하세요.
- 실행하면 브라우저에 프로그램 화면이 열립니다. 종료할 때는 화면의 **종료** 버튼을 누릅니다.

현재 소스의 기본 설정은 **R13 · 720p · 비전 토큰 560 · 영상 최대 3프레임**입니다. Windows 실행 파일은 [GitHub Actions의 성공한 빌드](https://github.com/yooongZa/filename/actions/workflows/desktop.yml)에서 `filename-windows-x64`를 다운로드할 수 있습니다. Mac은 아래 빌드 명령으로 현재 소스의 앱을 만듭니다.

[기존 v0.1.0 배포본](https://github.com/yooongZa/filename/releases/tag/v0.1.0)은 Mac·Windows용으로 제공되며, R13 변경이 포함되기 전 버전입니다. Mac 배포 앱은 Apple 공증을 받지 않았고 Windows 배포 앱은 코드 서명이 없어 운영체제의 실행 확인 절차가 나타날 수 있습니다.

## 사용 방법

1. 사진·영상이 있는 폴더를 선택하고 **분석 시작**을 누릅니다.
2. 제안된 태그 5개와 파일명을 확인하고 필요한 내용을 수정합니다.
3. 변경할 파일을 선택하고 **검토 완료 · 변경**을 누릅니다.
4. 원래 이름으로 돌아가려면 마지막으로 분석한 폴더를 확인한 뒤 **변경 이력 되돌리기**를 누릅니다.

기본값은 **변경 전 검토 켜짐 / 자동 변경 꺼짐**입니다. 검토를 끄고 자동 변경을 켜면 분석 후 유효한 결과를 자동 반영합니다. 검토가 켜져 있으면 검토가 우선합니다.

처리 이력에 있는 파일과 같은 5태그 규칙으로 이름이 붙은 파일은 다음 분석에서 건너뜁니다. 이름이 충돌하면 덮어쓰지 않습니다. 파일 내용과 확장자는 보존하며, 선택한 폴더의 `.filename/`에 변경 이력과 분석 기록을 저장합니다. 되돌리기에 필요한 이력이므로 이 폴더를 보관하세요.

지원 사진은 **JPG·JPEG·PNG·WEBP·BMP**, 영상은 **MP4·MOV·M4V·MKV·WEBM·AVI**입니다. HEIC·RAW는 현재 지원하지 않습니다.

## 모델 준비

모델은 **Gemma 4 12B IT QAT · Q4_0**입니다. 지정한 경로, 앱 캐시, LM Studio 기본 모델 폴더에서 모델과 이미지 프로젝터를 찾습니다. 없으면 분석 시작 시 [Google 공식 GGUF](https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf)를 내려받습니다. LM Studio를 실행할 필요는 없습니다.

모델은 배포 ZIP에 포함하지 않습니다. 처음 준비할 때 인터넷 연결과 약 7.2GB 이상의 모델 저장 공간이 필요합니다. 실행 엔진이 없으면 llama.cpp `b11642`도 준비합니다. Mac은 Metal, Windows는 Vulkan을 사용하며, Windows에는 Vulkan을 지원하는 GPU 드라이버가 필요합니다. 이후 이미지·영상 분석은 로컬에서 실행됩니다.

모델·실행 엔진을 직접 지정하려면 사용자 설정의 `runtime.model_path`, `runtime.mmproj_path`, `runtime.server_path`에 경로를 넣습니다. 자동 다운로드는 `runtime.auto_download`로 제어합니다.

## R13 기본 설정

Mac과 Windows가 같은 설정 파일·프롬프트·출력 규칙을 사용합니다.

| 항목 | 기본값 |
|---|---|
| 이미지 크기 | 짧은 변 720px, 원본보다 확대하지 않음 |
| 비전 토큰 | 이미지 한 장당 상한 요청 560 |
| 영상 프레임 | 20%·50%·80% 지점, 최대 3장 |
| 프레임 추출 | 가까운 keyframe(키프레임) 우선, 부족하면 일반 프레임 보충 |
| Thinking(추론 과정 출력) | OFF |
| 최대 출력 / 문맥 길이 | 5000 / 8192 토큰 |
| temperature(생성 무작위성) / seed(시드) | 0 / 42 |
| 동시 요청 | 1 |
| GPU 레이어 | 38 |
| 엔진 배치 / 마이크로배치 | 576 / 576 |
| 서버 시작 / 추론 대기 시간 | 240초 / 600초 |

영상은 FFmpeg로 추출합니다. 목표 시점과 가까운 키프레임의 거리가 `min(0.5초, 영상 길이 × 0.05)` 이내이고 중복되지 않으면 사용합니다. 멀거나 중복되면 목표 시점에 가장 가까운 일반 프레임을 사용합니다. 실제 시각이 같은 프레임은 한 장만 전달하므로 매우 짧은 영상은 3장보다 적을 수 있습니다.

## 설정과 프롬프트 수정

첫 실행 시 다음 사용자 폴더에 편집 가능한 설정을 복사합니다.

- Mac: `~/Library/Application Support/filename/`
- Windows: `%LOCALAPPDATA%/filename/`

| 파일 | 설정 내용 |
|---|---|
| `configs/app.json` | 검토·자동 변경, 하위 폴더 포함, 모델·엔진 경로와 실행 옵션 |
| `configs/profiles/gemma4_12b.json` | 이미지 크기, 프레임 추출, 토큰 예산, 문맥 길이 |
| `assets/prompt_v3_five_tags.txt` | 모델에 전달하는 프롬프트 |
| `assets/schema_v3_five_tags.json` | `keywords`, `reason`, `place_name` 출력 규칙 |

화면의 **설정 파일** 버튼으로 `app.json`을 열 수 있습니다. `profile_path`는 추론 설정 파일을 가리키며, 프로필의 `prompt_path`와 `schema_path`는 프로필 파일 기준 상대 경로입니다. 설정은 분석 시작마다 다시 읽습니다. 태그는 중복 없는 5개를 사용하며, 경로 문자와 밑줄을 포함할 수 없습니다.

하위 폴더도 포함하려면 `recursive: true`, 이력이 없는 5태그 파일명도 분석하려면 `skip_tagged_names: false`로 설정합니다. `video_extraction.seconds_per_frame`은 `interval` 모드에서만 사용하며 R13의 `nearest_keyframe` 모드에서는 사용하지 않습니다.

**기존 설치를 업데이트해도 사용자 설정은 보존됩니다.** R13으로 전환할 때는 현재 소스로 빌드한 앱과 함께 사용자 폴더의 프로필을 갱신하고, `app.json`의 `runtime` 수치를 위 표에 맞추세요. 직접 지정한 모델·엔진 경로와 검토 옵션은 유지할 수 있습니다.

## 소스로 실행

Python 3.12에서 실행합니다.

```bash
git clone https://github.com/yooongZa/filename.git
cd filename
python -m venv .venv
# Mac: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-app.txt
python app.py
```

`python app.py --mock`은 모델 없이 화면과 분석 흐름을 확인하는 모의 실행입니다. 별도 테스트 폴더에서 사용하세요.

```text
app.py                  프로그램 시작
filename_app/           화면, 분석 처리, 모델 준비, 이름 변경
filename_bench/         앱이 사용하는 전처리와 모델 연결 모듈
configs/                앱 설정과 R13 프로필
assets/                 프롬프트와 JSON 출력 규칙
scripts/                실행 파일 빌드와 패키징 검사
tests/                  프로그램 회귀 테스트
```

## 테스트와 빌드

```bash
python -m unittest discover -s tests -v
python -m pip install "pyinstaller>=6,<7"
python scripts/build_desktop.py
```

각 운영체제에서 빌드합니다. Mac은 `dist/filename.app`, Windows는 `dist/filename/filename.exe`가 생성됩니다. Windows 자동 빌드는 [GitHub Actions](https://github.com/yooongZa/filename/actions/workflows/desktop.yml)에서 실행합니다.

```bash
# Mac 패키징 검사
python scripts/smoke_desktop.py dist/filename.app/Contents/MacOS/filename
# Windows 패키징 검사
python scripts/smoke_desktop.py dist/filename/filename.exe
```
