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

검토·자동 변경 옵션은 화면에서도 바꿀 수 있습니다. 검토가 켜져 있으면 자동 변경보다 검토가 우선합니다. 모델 경로를 직접 지정할 때는 해당 모델과 프로젝터 경로를 함께 지정하세요.

## 추론 설정: configs/profiles/gemma4_12b.json

| 항목 | 기본값 | 의미 |
|---|---|---|
| `baseline.short_edge_px` | `720` | 입력 이미지의 짧은 변. 원본보다 확대하지 않음 |
| `baseline.vision_token_budget` | `280` | 이미지 한 장의 시각 토큰 상한 요청 |
| `baseline.frames` | `"all"` | 추출 후보 모두 사용. 정수로 최대 장수 지정 가능 |
| `baseline.selection` | `"uniform"` | `uniform`, `blur`, `dedup`, `both` 중 선택 |
| `baseline.max_new_tokens` | `512` | 최대 출력 토큰 |
| `baseline.context_per_request` | `8192` | 입력·출력을 포함한 문맥 길이 |
| `baseline.temperature` | `0` | 생성 무작위성 |
| `video_extraction.seconds_per_frame` | `10` | 영상 프레임 추출 간격 |
| `video_extraction.max_frames` | `5` | 영상 한 편의 최대 추출 후보 수 |

앱은 한 번에 파일 하나씩 분석합니다. 이 값들은 시작 설정이며, 환경별 실험 결과에 따라 조정하세요. 한 폴더를 분석하는 동안 모델을 재사용합니다.

## 프롬프트와 출력 규칙

추론 설정의 `prompt_path`와 `schema_path`는 해당 설정 파일 기준 상대 경로입니다. 기본 파일은 `assets/prompt_v3_five_tags.txt`와 `assets/schema_v3_five_tags.json`입니다.

프롬프트는 별도 텍스트 파일에서 불러옵니다. 태그 수 규칙은 프롬프트·JSON Schema·파일명 처리 코드에서 **5개**로 검사하므로, 파일명 규칙을 그대로 쓸 때는 이 조건을 유지하세요.

Colab·로컬 실험과 같은 조건을 쓰려면 `configs/profiles/gemma4_12b.json` 및 참조하는 프롬프트·Schema 파일을 같은 상대 구조로 복사합니다. 실험 저장소에서는 `--profile configs/profiles/gemma4_12b.json`으로 선택합니다.
