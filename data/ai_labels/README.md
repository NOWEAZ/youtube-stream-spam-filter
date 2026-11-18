# 팀 공유용 AI 생성 라벨 — pilot-v1

이 폴더는 Git으로 공유하는 고정 데이터 스냅샷입니다. 원본 수집 파일·API 키·실행 DB는 계속 `.local/`과 `.env`에만 보관합니다.

- 댓글 22건: UCI 영문 중심 댓글 20건 + 사용자가 제공한 한국어 광고 비판 사례 2건.
- OpenCode Go / `minimax-m3.1-flash` / `draft-label-v1`.
- AI 판정 normal 17건, spam 5건, uncertain 0건.
- 모든 라벨은 `label_source=ai`, `review_status=unreviewed`, `human_label=null`입니다. 사람의 정답이나 실제 분류 정확도의 기준이 아닙니다.

## 같은 데이터로 테스트

프로젝트 루트에서 실행합니다. API 키나 로컬 HTTP 서버가 필요하지 않으며 OpenCode를 호출하지 않습니다.

```powershell
git pull origin main
python scripts/check_ai_labels.py
```

규칙 판정과 AI 판정이 다른 댓글의 ID·판정 근거를 `reports/ai-label-comparison.json`에서 확인할 수 있습니다. 수치는 **AI 판정과의 일치도**이고, 사람의 정답 기준 정확도가 아닙니다. 각 댓글은 독립 세션으로 검사하고 악성 URL 목록은 비워 두므로, 실행 순서나 개인 목록 때문에 결과가 달라지는 것을 줄였습니다. 프로그램 버전을 바꾸면 결과는 달라질 수 있습니다.

Python 명령이 없는 이 컴퓨터에서는:

```powershell
& "$env:USERPROFILE/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe" scripts/check_ai_labels.py
```

## 파일과 검토 방법

`pilot-v1.jsonl`은 한 줄에 댓글 하나와 AI 라벨·원래 AI 근거·출처·모델·프롬프트 정보를 포함합니다. 사람 검토를 아직 하지 않았으므로 AI 근거의 오류나 어색한 표현도 고치지 않았습니다. 수집 시각과 내부 출처 ID는 공유본에서 제외했습니다.

`pilot-v1.manifest.json`은 체크섬, 건수·분포·출처·라이선스·변환 내역을 기록합니다. 테스트 스크립트는 체크섬과 미검토 라벨 상태부터 확인합니다. `.gitattributes`로 JSONL 줄바꿈을 LF로 고정해 Windows·macOS 간 체크섬을 일치시킵니다. 라벨을 검토·변경할 때는 이 파일을 덮어쓰기보다 새 버전으로 만들고, AI 원래 의견과 사람의 최종 라벨을 별도 필드로 유지해 주세요. 이 스냅샷으로 학습하고 같은 스냅샷의 결과를 최종 성능으로 보고하지 않습니다.

## 출처와 이용 조건

UCI 20건의 출처: Alberto, T. & Lochter, J. (2015). [YouTube Spam Collection](https://archive.ics.uci.edu/dataset/380/youtube+spam+collection). UCI Machine Learning Repository. DOI: [10.24432/C58885](https://doi.org/10.24432/C58885). [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

원본에서 표본을 추출하고, HTML·연락처·URL 경로/쿼리를 마스킹했으며 원래 정답 CLASS를 제외한 뒤 AI 라벨을 추가했습니다. 작성자 열과 API 키를 포함하지 않습니다. 자동 마스킹은 완전한 익명화가 아니며 텍스트에 맥락 손실이 있을 수 있습니다.

한국어 2건은 프로젝트 소유자가 팀 테스트를 위한 Git 게시를 요청한 제공 사례입니다. 이 두 원문에 UCI 라이선스나 별도의 포괄적 라이선스를 부여한 것으로 간주하지 않습니다. 전체 22건은 소규모·편향된 초벌 표본이며 실제 유튜브 댓글 분포를 대표하지 않습니다.
