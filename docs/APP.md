# filename 로컬 파일 정리

`폴더 선택 → 분석 → 파일명 검토 → 변경` 네 단계입니다. 사진 한 장 또는 영상 한 편에서 태그 5개를 추출해 `원래이름__태그1_태그2_태그3_태그4_태그5.확장자`로 바꿉니다. 원래 이름과 확장자, 사진·영상 내용은 보존합니다.

## 실행

- Mac: `filename.app`을 엽니다. Apple Silicon 빌드는 M1~M4 계열 Mac용입니다.
- Windows x64: ZIP을 전부 압축 해제하고 `filename/filename.exe`를 엽니다. `_internal` 폴더를 함께 보관합니다.
- 브라우저에서 로컬 화면이 열립니다. 프로그램 종료는 화면 오른쪽 위 `종료`를 누릅니다.
- 개발 실행: `python -m pip install -r requirements-app.txt` 후 `python app.py`.
- 화면 확인: `python app.py --mock`. 모의 실행은 실제 파일명을 변경하지 않습니다.

Mac 앱은 개발용 ad-hoc 서명이며 Apple 공증은 하지 않았습니다. 다운로드한 앱에 확인 창이 뜨면 macOS의 앱 실행 허용 절차가 필요할 수 있습니다. Windows도 배포용 코드 서명이 없는 개발 빌드입니다.

## 모델과 실행 엔진

선택 모델은 **Gemma 4 12B QAT, Q4_0**입니다. LM Studio는 실행할 필요가 없습니다.

1. config에 지정한 `model_path`, `mmproj_path`를 먼저 사용합니다.
2. 비어 있으면 `~/.lmstudio/models`, 앱 캐시, `model_search_dirs`에서 해당 QAT Q4_0 모델과 짝을 찾습니다. 다른 모델이나 양자화는 자동으로 선택하지 않습니다.
3. 없으면 Google의 공식 GGUF 모델·프로젝터를 내려받습니다. 합계 약 7.15 GB이며 `.part`로 이어받고 SHA-256을 확인합니다.
4. `llama-server`가 없으면 고정한 공식 릴리스 `b11642`를 준비합니다. Mac은 Metal, Windows x64는 Vulkan 빌드입니다. Vulkan을 지원하는 최신 GPU 드라이버가 필요합니다. CUDA 실행 파일을 직접 쓰려면 `server_path`로 지정합니다.
5. 설치가 끝나면 추론은 `127.0.0.1`에서 실행됩니다. 파일 내용은 외부 분석 API로 전송하지 않습니다.

모델·엔진 자동 다운로드는 분석 시작을 눌렀을 때 진행합니다. 다운로드 중 `중단`하면 다음 실행에서 이어받습니다. 인증·네트워크·디스크 공간 오류는 화면에 표시됩니다. `auto_download: false`로 끄고 경로를 직접 지정할 수도 있습니다.

모델 식별·다운로드 출처:

- [Google QAT GGUF](https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf), revision `29d097773436b69ff9feafd636ab4cf873786537`
- [llama.cpp 실행 파일](https://github.com/ggml-org/llama.cpp/releases/tag/b11642)
- 기존 [LM Studio GGUF](https://huggingface.co/lmstudio-community/gemma-4-12B-it-QAT-GGUF)도 같은 폴더의 mmproj와 함께 재사용합니다.

R13은 Windows RTX 3070 8GB에서 사진 20장·영상 20편의 실제 추론과 이름 변경을 확인했습니다. 분석 시간은 455.39초, 관측한 GPU 메모리 최대 사용량은 7,175MiB였습니다. 환경별 속도·메모리는 달라질 수 있으며, [검증 기록](R13_VALIDATION.md)에 측정 범위를 정리했습니다. 앱은 한 번에 파일 하나씩 분석하며 선택한 폴더를 처리하는 동안 모델을 재사용합니다.

## 두 가지 변경 옵션

| 변경 전 검토 | 자동 변경 | 동작 |
|---|---|---|
| 켜짐 | 꺼짐 | 기본값. 결과 검토 후 `검토 완료 · 변경`을 누릅니다. |
| 켜짐 | 켜짐 | 검토 우선. 버튼을 눌러 검토를 마친 파일만 변경합니다. |
| 꺼짐 | 꺼짐 | 분석 결과를 표시하고 수동 변경을 기다립니다. |
| 꺼짐 | 켜짐 | 형식·태그 5개 검사를 통과한 결과를 자동 변경합니다. |

자동 모드도 오류, 응답 잘림, 중복 태그, 태그 수 부족, 이름 충돌이 있으면 해당 파일을 그대로 남깁니다. 태그 내용의 사실성은 자동 형식 검사만으로 보장되지 않으므로 검토 옵션을 사용할 수 있습니다.

## 설정과 프롬프트 위치

처음 실행할 때 아래 사용자 폴더에 편집 가능한 설정을 복사합니다. 앱을 다시 빌드해도 사용자 설정은 덮어쓰지 않습니다.

- Mac: `~/Library/Application Support/filename/`
- Windows: `%LOCALAPPDATA%/filename/`

```text
configs/app.json                         # 앱 옵션·모델 경로·엔진 옵션
configs/profiles/gemma4_12b.json           # 해상도·토큰·프레임·전처리
assets/prompt_v3_five_tags.txt            # v3 기반 태그 5개 프롬프트
assets/schema_v3_five_tags.json           # keywords/reason/place_name
```

`설정 파일` 버튼으로 app.json을 엽니다. 추론 조건은 이 파일의 `profile_path`가 가리키는 JSON에서 바꿉니다. 파일은 분석 시작마다 다시 읽습니다. 화면에서 선택한 검토·자동 변경 옵션은 app.json에 저장됩니다.

| 설정 | 기본값 | 의미 |
|---|---|---|
| `baseline.short_edge_px` | 720 | 입력 이미지의 짧은 변, 원본보다 확대하지 않음 |
| `baseline.vision_token_budget` | 560 | 이미지 한 장의 시각 토큰 상한 요청 |
| `baseline.frames` | 3 | 최대 3프레임 전달; 사진은 1장 |
| `baseline.selection` | `uniform` | `uniform`, `blur`, `dedup`, `both` |
| `baseline.max_new_tokens` | 5000 | 출력 토큰 상한 |
| `baseline.context_per_request` | 8192 | 입력·출력 합계 문맥 길이 |
| `baseline.temperature` | 0 | 생성 무작위성 |
| `video_extraction.mode` | `nearest_keyframe` | 영상 20%·50%·80% 지점의 가까운 키프레임 우선, 부족하면 일반 프레임 보충 |
| `video_extraction.seconds_per_frame` | 10 | `interval` 모드에서만 사용하는 추출 간격 |
| `video_extraction.max_frames` | 3 | 영상 한 편의 최대 후보 수 |
| `recursive` | false | 하위 폴더 포함 여부 |
| `skip_tagged_names` | true | 이력이 없어도 동일한 5태그 파일명 건너뜀 |

현재 기본값은 R13입니다. GPU 레이어 38, 엔진 배치·마이크로배치 576, Thinking OFF를 함께 사용합니다. 앱은 동시 요청을 1로 제한합니다. 프레임 선택 방식과 기존 설치 적용 방법은 [설정 안내](CONFIG.md)를 참고하세요.

## 이미 처리한 파일과 복원

선택한 폴더 안에 `.filename/`을 만듭니다.

- `history.sqlite3`: 원래 이름·새 이름·파일 해시·변경 상태
- `analyses/`: 파일별 응답 원문, 사용한 config·프롬프트·Schema·서버 로그
- `frames/`: 원본 영상에서 추출한 후보 프레임

완료 이력에 있는 경로 또는 `원래이름__태그1_…_태그5` 형식은 다음 분석에서 건너뜁니다. 일반적인 다른 도구의 임의 이름 변경은 판별하지 못합니다. 처리 여부는 파일 내용으로 추정하지 않습니다.

이름 충돌은 덮어쓰지 않고 오류로 표시합니다. 변경 직전 원본 해시를 재확인합니다. `변경 이력 되돌리기`는 선택한 폴더의 완료된 이름 변경을 최근 순서부터 복원합니다. 이후 내용이 바뀌거나 원래 이름에 다른 파일이 있으면 그 파일은 보존합니다. `.filename`을 지우면 이력 기반 복원을 할 수 없습니다.

지원 사진: JPG·JPEG·PNG·WEBP·BMP. 지원 영상: MP4·MOV·M4V·MKV·WEBM·AVI. HEIC·RAW는 현재 지원하지 않습니다.

## 개발·검증

```bash
python -m unittest discover -s tests -v
python app.py --mock
python scripts/build_desktop.py
python scripts/smoke_desktop.py dist/filename/filename.exe
# Mac: dist/filename.app/Contents/MacOS/filename
```

Windows 패키징은 `.github/workflows/desktop.yml`에서 Windows runner로 수행합니다. 모의 패키징 검증과 실제 GPU 품질·메모리 검증은 별개입니다.

Based-On: none — 기존 filename의 프레임 전처리·서버 연결을 재사용한 project_extension입니다. LMS 원문은 포함하지 않습니다.
