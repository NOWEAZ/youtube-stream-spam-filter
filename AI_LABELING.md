# OpenCode 초벌 라벨 관리

이 기능은 오프라인 데이터 작성 도구입니다. 기존 확장 프로그램의 판정·자동 접기·규칙 가중치를 바꾸거나 학습시키지 않습니다. 정상·스팸·모호한 댓글을 함께 포함한 입력을 OpenCode가 검토하며, 라이브 필터의 `검토 필요`에만 대상을 제한하지 않습니다.

## 저장과 판정 기준

- `ai_label`: `normal`(일반 감상·토론·질문·광고 비판 등), `spam`(무관한 광고·자기 홍보·피싱 등), `uncertain`(맥락 부족·마스킹으로 불확실).
- `label_source=ai`, `review_status=unreviewed`, `human_label=null`, `eligible_for_gold_evaluation=false`로 저장합니다. AI 결과를 사람의 정답으로 복사하지 않습니다.
- AI 근거, 모델, OpenCode 제공 방식, 프롬프트 버전·해시, 마스킹 버전, 시각, 입력 출처를 기록합니다.
- 기존 규칙 점수·판정, UCI의 원래 CLASS, 작성자·날짜를 모델에 보내지 않습니다. AI 프롬프트와 판정은 라이브 필터의 도구 기반 검토와 분리됩니다.
- 연락처·이메일·@핸들·URL의 사용자정보·경로·쿼리를 마스킹합니다. 주소의 호스트와 연락 유도 표현은 가능한 한 유지합니다. 자동 마스킹은 완전한 익명화가 아니며 분류에 필요한 문맥을 없앨 수 있습니다.

수집 원본·실행 DB·기본 출력은 `.local/labels/`에 생성하며 Git과 배포 ZIP에서 제외합니다. 팀원의 재현 테스트를 위해 개인정보를 점검한 고정 스냅샷 22건을 [`data/ai_labels/`](data/ai_labels/README.md)에 별도로 공유합니다. 이 공유본은 Git과 배포에 포함되며, 실제 API 키·DB·수집 원본은 포함하지 않습니다.

| 파일 | 용도 |
| --- | --- |
| `drafts.sqlite3` | 샘플·모델/프롬프트별 AI 판정 이력 |
| `ai-drafts.jsonl` | AI 생성 라벨과 검토 상태 전체 내보내기 |
| `ai-drafts.summary.json` | 라벨·처리 상태·언어별 집계 |
| `ai-drafts.html` | 사람이 읽기 쉬운 로컬 검토 보고서 |

HTML 보고서는 읽기 전용입니다. 체크하거나 읽는 것만으로 사람이 검토 완료한 정답으로 바뀌지 않습니다. 정답 검토·학습 데이터 내보내기는 후속 단계입니다. 이 단계에서는 훈련·검증·최종 테스트 분할을 만들지 않으며, 이후 Shingling/LSH로 유사 댓글을 묶어 분할해야 합니다.

## 입력과 실행

프로젝트 루트의 `.env`에서 기존 OpenCode 키를 읽습니다. 입력은 한 줄에 JSON 하나인 JSONL입니다. `text`는 필수이고, `source`, `source_id`는 선택입니다. 원본 댓글 입력 파일도 `.local/labels/`에 보관해 주세요.

```json
{"text":"Thanks for the clear explanation!","source":"local-import","source_id":"sample-1"}
```

가져오기와 예상 호출 수 확인만 수행하려면:

```powershell
python scripts/draft_label.py --input .local/labels/my-comments.jsonl --max-requests 20
```

실제 OpenCode 요청을 보내려면:

```powershell
python scripts/draft_label.py --run --max-requests 20 --requests-per-minute 6
python scripts/label_report.py
```

기본 최대 20회·분당 6회, 댓글당 API 요청 1회·타임아웃 30초입니다. 결과는 매 요청 직후 SQLite와 JSONL에 저장합니다. 중단 후 재실행하면 완료한 동일 샘플/모델/프롬프트는 건너뜁니다. 오류를 정상으로 라벨링하지 않으며 기본적으로 재시도하지 않습니다. 명시적으로 재시도하려면 `--retry-errors`를 사용하세요. 연속 3회 오류 또는 401/402/403/429가 나오면 중단합니다. 새 모델 또는 새 프롬프트로 다시 판정하려면 입력을 다시 가져오며 이전 AI 이력은 보존합니다.

Python 명령이 없다면 이 컴퓨터에서는 아래 실행 파일로 `python`을 대신할 수 있습니다.

```powershell
& "$env:USERPROFILE/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe" scripts/draft_label.py --run --max-requests 20
```

## 공개 데이터 파일 준비

```powershell
python scripts/prepare_label_pilot.py --per-video 4
```

공식 UCI ZIP을 메모리에서 읽고 5개 영상에서 각 4건을 seed 42로 추출합니다. 원래 정답을 보고 표본을 고르지 않습니다. 정답·작성자·날짜·원본 ID는 저장하지 않으며 입력과 출처 manifest만 로컬에 저장합니다. 원본 ZIP은 디스크에 보관하지 않습니다. 기존 입력 파일을 덮어쓰지 않으므로 새 표본은 새 `--output`을 지정하세요.

출처: Alberto, T. & Lochter, J. (2015), [YouTube Spam Collection](https://archive.ics.uci.edu/dataset/380/youtube+spam+collection), DOI [10.24432/C58885](https://doi.org/10.24432/C58885), CC BY 4.0. 원본은 2010년대 영문 중심 댓글이므로 현재 한국어 유튜브나 새 광고 양식을 대표하지 않습니다. AI 재라벨을 원본 정답이나 실제 성능 평가와 혼동하지 않아야 합니다.

## 첫 실행 범위

공개 UCI 댓글 20건과 사용자가 제공한 한국어 광고 비판 사례 2건을 파일 출처로 구분해 총 22건을 초벌 라벨링합니다. 한국어 2건은 오탐 신고 사례이므로 무작위 한국어 표본이 아닙니다. 실제 완료 건수와 라벨 분포는 로컬 summary를 기준으로 확인하세요. 이 자료는 아직 학습용 초안이며 분류 정확도를 입증하지 않습니다.


## 팀원이 공유 데이터로 테스트하기

```powershell
python scripts/check_ai_labels.py
```

별도 키·서버·추가 API 호출 없이 공유 스냅샷의 체크섬과 상태를 확인한 다음 규칙 판정과 비교합니다. `reports/ai-label-comparison.json`에는 서로 다른 판정과 근거를 기록합니다. AI 판정을 정답으로 이름을 바꿔 실제 정확도로 보고하지 않습니다. 공유본은 `.local`의 실행 DB와 독립된 버전 파일입니다.


## 사용자 피드백과의 구분

v0.6.0의 [개인 라벨](PERSONAL_LABELS.md)은 확장에서 사용자가 직접 눌러 확인한 개인 선호 데이터이며 `label_source=user`입니다. 이 문서의 OpenCode 초벌 라벨은 `label_source=ai`로 유지합니다. 개인 피드백을 AI 원본에 덮어쓰거나, 한 사용자의 라벨을 다른 사용자에게 자동 적용하지 않습니다.
