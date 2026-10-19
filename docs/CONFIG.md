# 설정 변경

앱은 `configs/app.json`과 이 파일의 `profile_path`가 가리키는 추론 설정을 분석 시작마다 읽습니다. 배포 앱에서는 저장소 파일 대신 사용자 폴더에 복사된 설정을 수정하세요.

- Mac: `~/Library/Application Support/filename/`
- Windows: `%LOCALAPPDATA%/filename/`

## 앱 설정: configs/app.json

| 항목 | 기본값 | 의미 |
|---|---|---|
| `review_before_rename` | `true` | 이름 변경 전 사용자 검토 |
| `auto_rename` | `false` | 검토를 끈 경우 분석 후 자동 이름 변경 |
| `recursive` | `false` | 하위 폴더 포함 |
| `skip_tagged_names` | `true` | 같은 5태그 파일명 규칙인 파일 건너뛰기 |
| `runtime.auto_download` | `true` | 모델·실행 엔진이 없으면 다운로드 |
| `runtime.model_path` | 빈 문자열 | 이미 설치한 GGUF 모델 경로 |
| `runtime.mmproj_path` | 빈 문자열 | 모델과 맞는 이미지 프로젝터 경로 |
| `runtime.server_path` | 빈 문자열 | 직접 설치한 llama-server 경로 |
| `runtime.model_search_dirs` | `[]` | 모델을 추가로 찾을 폴더 목록 |
| `runtime.gpu_layers` | `38` | GPU에 올릴 모델 레이어 수 |
| `runtime.engine_token_batch` | `576` | 실행 엔진의 논리 배치 토큰 수 |
| `runtime.engine_microbatch` | `576` | 실행 엔진의 물리 배치 토큰 수 |
| `runtime.startup_timeout_s` | `240` | 서버 시작 대기 시간(초) |
| `runtime.request_timeout_s` | `600` | 파일별 추론 응답 대기 시간(초) |

검토·자동 변경 옵션은 화면에서도 바꿀 수 있습니다. 검토가 켜져 있으면 자동 변경보다 검토가 우선합니다. 모델 경로를 직접 지정할 때는 해당 모델과 프로젝터 경로를 함께 지정하세요.

## 추론 설정: configs/profiles/gemma4_12b.json

기본값은 Windows RTX 3070 8GB에서 검증한 **R13**입니다. 720p는 입력의 짧은 변 720px을 뜻합니다. 사진 또는 영상의 프레임들을 한 번의 모델 요청에 넣고 태그를 생성합니다.

| 항목 | 기본값 | 의미 |
|---|---|---|
| `baseline.short_edge_px` | `720` | 입력 이미지의 짧은 변. 원본보다 확대하지 않음 |
| `baseline.vision_token_budget` | `560` | 이미지 한 장의 시각 토큰 상한 요청 |
| `baseline.frames` | `3` | 모델에 전달할 최대 프레임 수. 사진은 1장 |
| `baseline.selection` | `"uniform"` | `uniform`, `blur`, `dedup`, `both` 중 선택 |
| `baseline.max_new_tokens` | `5000` | 최대 출력 토큰 |
| `baseline.context_per_request` | `8192` | 입력·출력을 포함한 문맥 길이 |
| `baseline.temperature` | `0` | 생성 무작위성 |
| `baseline.concurrency` | `1` | 동시 요청 수 |
| `video_extraction.mode` | `"nearest_keyframe"` | 목표 시점에 가까운 프레임 추출 |
| `video_extraction.fractions` | `[0.2, 0.5, 0.8]` | 영상 길이의 20%, 50%, 80% 지점 |
| `video_extraction.max_keyframe_distance_s` | `0.5` | 목표 시점과 키프레임의 최대 거리(초) |
| `video_extraction.max_keyframe_distance_fraction` | `0.05` | 영상 길이에 비례한 최대 거리 |
| `video_extraction.seconds_per_frame` | `10` | `interval` 모드에서만 사용하는 추출 간격 |
| `video_extraction.max_frames` | `3` | 영상 한 편의 최대 추출 후보 수 |

FFmpeg로 실제 프레임 시각을 읽습니다. 각 목표 시점에서 가장 가까운 keyframe(키프레임)이 `min(0.5초, 영상 길이 × 0.05)` 이내이고 다른 목표와 중복되지 않으면 사용합니다. 멀거나 중복되면 목표 시점에 가장 가까운 일반 프레임으로 보충합니다. 실제 시각이 같은 프레임은 한 장만 전달하므로 매우 짧은 영상은 3장보다 적을 수 있습니다. 알고리즘 식별자는 `nearest-keyframe-target-fallback-v2`입니다.

Thinking(추론 과정 출력)은 서버의 `--reasoning off --reasoning-format deepseek`와 요청의 `enable_thinking: false`, `reasoning_effort: none`으로 끕니다. 앱은 한 번에 파일 하나씩 분석하고, 한 폴더를 분석하는 동안 모델을 재사용합니다. R13의 측정 결과는 [검증 기록](R13_VALIDATION.md)에 있습니다.

## 기존 설치에 R13 적용

앱을 업데이트해도 기존 사용자 설정은 보존됩니다. 기존 설치에서는 사용자 폴더의 `configs/app.json` 중 위 `runtime` 수치와 `configs/profiles/gemma4_12b.json`을 R13 값으로 맞추세요. 지정한 모델·엔진 경로와 검토·자동 변경 옵션은 유지할 수 있습니다. 새 프레임 추출과 Thinking OFF 실행 옵션은 R13 소스 또는 이를 빌드한 실행 파일이 필요합니다. 기본 프롬프트·Schema의 내용은 기존 버전과 같습니다.

## 프롬프트와 출력 규칙

추론 설정의 `prompt_path`와 `schema_path`는 해당 설정 파일 기준 상대 경로입니다. 기본 파일은 `assets/prompt_v3_five_tags.txt`와 `assets/schema_v3_five_tags.json`입니다.

프롬프트는 별도 텍스트 파일에서 불러옵니다. 태그 수 규칙은 프롬프트·JSON Schema·파일명 처리 코드에서 **5개**로 검사하므로, 파일명 규칙을 그대로 쓸 때는 이 조건을 유지하세요.

Colab·로컬 실험과 같은 조건을 쓰려면 `configs/profiles/gemma4_12b.json` 및 참조하는 프롬프트·Schema 파일을 같은 상대 구조로 복사합니다. 실험 저장소에서는 `--profile configs/profiles/gemma4_12b.json`으로 선택합니다.
