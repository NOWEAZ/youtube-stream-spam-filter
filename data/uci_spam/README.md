# UCI YouTube Spam Collection 디렉터리 (`data/uci_spam/`)

이 디렉터리에는 UCI YouTube Spam Collection 원본 CSV 파일들을 배치합니다.
* `Youtube01-Psy.csv`
* `Youtube02-KatyPerry.csv`
* `Youtube03-LMFAO.csv`
* `Youtube04-Eminem.csv`
* `Youtube05-Shakira.csv`

> **원본 데이터 보호 수칙:** 다운로드한 원본 CSV 파일은 절대 직접 수정하지 마십시오.

### Near-duplicate 그룹 단위 분할 스크립트 실행
데이터 유출(Data Leakage)을 방지하기 위해 단일 댓글 단위가 아닌 문자 5-shingle Jaccard 유사도 기반 클러스터 단위로 분할합니다 (`SEED = 42`).
```bash
python data/split_dataset.py
```
* **출력 파일:**
  * `split_reference.json`: LSH 영구 참조 스팸 등록용
  * `split_validation.json`: 임계값(Threshold) 조정용
  * `split_test.json`: 최종 성능 평가용
  * `split_summary.json`: 분할 요약 및 클러스터 통계
