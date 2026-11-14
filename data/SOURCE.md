# 데이터셋 출처 및 정제 기록 (`data/SOURCE.md`)

> **주의:** `data/` 디렉터리 내의 원본 데이터 파일은 절대 직접 수정하지 마십시오.
> 아직 전체 원본 수집·정제가 완료되지 않은 외부 데이터셋의 건수는 지침에 따라 `(확인 필요)`로 표기합니다.

---

## 1. 악성 URL 데이터셋 (`data/urlhaus_phishtank/`)

| 항목 | URLhaus | PhishTank | 로컬 부트스트랩/검증용 샘플 (`sample_urls_sorted.txt`) |
| :--- | :--- | :--- | :--- |
| **출처 (URL)** | `https://urlhaus.abuse.ch/downloads/` | `https://phishtank.org/developer_info.php` | 프로젝트 기본 동작/단위 검증용 내장 샘플 |
| **수집 시점** | `(확인 필요)` | `(확인 필요)` | 2026-11-14 |
| **라이선스** | CC0 1.0 Universal (Public Domain) | PhishTank Terms of Use (비상업 연구/방어 목적) | MIT / 내부 테스트용 |
| **원본 건수** | `(확인 필요)` | `(확인 필요)` | 25건 |
| **정규화·중복 제거 후 건수** | `(확인 필요)` | `(확인 필요)` | 25건 (사전순 정렬 완료) |
| **비회원(정상) 질의셋 건수** | `(확인 필요)` | `(확인 필요)` | `benchmarks/run_benchmarks.py` 실행 시 `SEED = 42`로 분리 생성 |

* **정규화 규칙 (`src/normalizer.py`):**
  1. 스킴(`http://`, `https://`) 및 선행 `www.` 제거, 호스트명 소문자 변환
  2. 불필요한 추적 파라미터(`utm_source`, `utm_medium`, `utm_campaign`, `utm_term`, `utm_content`, `fbclid`, `gclid`, `si`, `feature`) 제거
  3. URL 말미 슬래시(`/`) 및 프래그먼트(`#...`) 제거
* **2차 디스크 조회 포맷:** 정규화 후 중복을 제거하고 사전순(UTF-8 바이트순)으로 정렬된 줄 단위 텍스트 파일(`*_sorted.txt`)을 사용하며, `src/bloom_filter.py`의 `DiskSortedURLStore`가 파일 오프셋 기반 이진 탐색으로 $O(\log M)$에 디스크에서 직접 확인합니다.

---

## 2. 라벨 댓글 데이터셋 (`data/uci_spam/`)

| 항목 | UCI YouTube Spam Collection |
| :--- | :--- |
| **출처 (URL)** | `https://archive.ics.uci.edu/dataset/380/youtube+spam+collection` |
| **수집 시점** | `(확인 필요)` |
| **라이선스** | CC BY 4.0 |
| **원본 파일 구성** | Psy, KatyPerry, LMFAO, Eminem, Shakira 5개 영상 댓글 CSV (총 약 1,956건, 정확한 로드 건수는 `(확인 필요)`) |
| **정규화·Near-duplicate 그룹화 후 건수** | `(확인 필요)` |
| **데이터 분할 (Reference / Validation / Test)** | Near-duplicate 그룹 단위 분할 (`SEED = 42`), 분할별 건수 `(확인 필요)` |

* **데이터 유출(Data Leakage) 방지 원칙:**
  * 동일하거나 문자 5-shingle Jaccard 유사도가 높은 변형 댓글은 동일한 클러스터(Near-duplicate Group)로 묶어 Reference(참조 인덱스 등록용), Validation(임계값 튜닝용), Test(최종 성능 보고용) 중 한 곳에만 배정합니다.

---

## 3. 제어 실험용 합성·재생 스트림 (`data/synthetic_stream/`)

| 항목 | 설명 |
| :--- | :--- |
| **파일 경로** | `data/synthetic_stream/sample_stream.json` |
| **생성 시드** | `SEED = 42` |
| **구성 건수** | 40건 (정상 댓글, 짧은 감탄사, 악성 URL 포함 댓글, 이모지/숫자 변형 홍보 도배 댓글, 경계선 보류 댓글 포함) |
| **용도** | 파이프라인 기능 검증, LSH 유사 댓글 후보 축소율/재현율 검증, DGIM 및 Flajolet-Martin 스트림 추정 오차 측정 |
