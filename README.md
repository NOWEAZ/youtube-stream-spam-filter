# YouTube Stream Spam Filter (`youtube-stream-spam-filter`)

> **A real-time YouTube spam comment filtering Chrome Extension powered by Bloom Filters, Stream Sketches (DGIM, Flajolet-Martin), and MinHash/LSH.**
> 
> **과목:** 빅데이터개론 (2026-2학기) | **팀원:** 2021270651 김재원, 2021270633 이창현, 2020270664 조승민
> **저장소:** `https://github.com/NOWEAZ/youtube-stream-spam-filter`

---

## 0. 코딩 에이전트(Agent) 구현 절대 수칙

이 저장소에서 작업하는 코딩 에이전트는 코드를 작성하거나 문서를 갱신하기 전에 반드시 아래 제약 사항을 준수해야 합니다.

1. **외부 LLM / Upstage Solar 연동 금지:** 본 프로젝트는 외부 LLM API를 사용하지 않으며, **순수 빅데이터 스트림·유사도 알고리즘(Bloom Filter, MinHash, LSH, DGIM, Flajolet-Martin)과 규칙 기반 신호 결합**만으로 동작합니다.
2. **핵심 알고리즘 직접 구현 (Black-box 라이브러리 금지):** `BloomFilter`, `Shingling`, `MinHash`, `LSH`, `DGIM`, `FlajoletMartin`은 반드시 **파이썬 3 표준 라이브러리만으로 직접 구현**해야 합니다(`pybloom`, `datasketch` 등 알고리즘이 내장된 외부 패키지 import 절대 금지).
3. **원본 데이터 보호 및 출처 기록:** `data/` 폴더 안의 원본 파일은 절대 수정하지 마십시오. 데이터 수집 시점, 라이선스, 정규화·중복 제거 후 실제 건수는 `data/SOURCE.md`에 기록하고 확인하지 못한 수치는 `(확인 필요)`로 표기합니다.
4. **개인정보 및 윤리 준수:** 작성자 표시명(Display Name)만으로 계정을 식별하거나 불필요한 개인 식별 정보를 디스크에 저장하지 마십시오.
5. **모든 수치의 재현성 보장:** 고정 시드(`SEED = 42`)를 사용하여 실험을 재현 가능하게 작성하고, 보고서의 모든 수치는 어느 스크립트와 어느 데이터 열에서 산출되었는지 명시해야 합니다.

---

## 1. 프로젝트 개요 및 해결할 문제

PC 크롬에서 유튜브 댓글을 읽는 사용자를 위해 스팸 의심 댓글을 실시간으로 표시하고 접어 주는(Folding/Blur) 확장 프로그램과 로컬 Python 분석 엔진을 개발합니다.

| 해결할 문제 | 엔지니어링 구현 방향 |
| :--- | :--- |
| **악성 URL 목록의 메모리 부담** | 수백만 건의 악성 URL 전체를 메모리에 해시셋(`set`)으로 올리는 대신, **Bloom Filter**로 1차 일치 후보를 $O(1)$에 선별하고 양성(Positive) 후보만 디스크의 원본 목록에서 재확인(2단계 검증)합니다. |
| **변형된 반복 홍보 댓글 우회** | 문구나 이모지를 살짝 바꾼 도배성 댓글을 $O(N^2)$ 전수 비교 없이 찾기 위해 문자 단위 **$k$-shingling, MinHash, LSH 밴딩**으로 유사 후보를 검색하고 실제 Jaccard 유사도와 홍보성 신호를 결합해 판별합니다. |
| **스크롤에 따른 실시간 댓글 스트림** | `MutationObserver`로 새로 로드되는 댓글을 감지하고 중복 처리를 방지하며, **DGIM**과 **Flajolet-Martin**을 통해 전체 이력을 저장하지 않고도 스트림 통계를 고정·로그 공간 안에서 추정합니다. |
| **자동 판별의 오탐(False Positive) 리스크** | 판정이 불확실한 경우 `판단 보류(Hold)` 상태를 지원하며, 사용자 화면에 **의심 근거 배지**와 **원문 보기(클릭 시 펼치기) 버튼**을 제공합니다. |

---

## 2. 지원 범위 및 운영 제약 (Scope & Constraints)

* **우선 지원 범위:** PC Chrome 브라우저의 **일반 유튜브 동영상 댓글 및 펼쳐진 답글(Replies)**을 대상으로 합니다. (라이브 채팅 및 Shorts 댓글은 기본 범위에서 제외하며 추가 목표로 구분합니다.)
* **클라이언트 로컬 동작 원칙:** 댓글 접기(마스킹)와 배지 표시는 확장 프로그램을 설치한 사용자의 화면(DOM)에만 적용됩니다. 실제 유튜브 댓글 삭제나 자동 신고 기능은 포함하지 않습니다.
* **시연 및 실행 환경:** 크롬 확장 프로그램과 로컬 Python HTTP 서버(`127.0.0.1:8000`)를 함께 실행합니다.
* **장애 격리 (Fail-safe):** 로컬 백엔드 서버와 연결이 끊기거나 타임아웃이 발생하면 확장 프로그램에 `분석 불가(오프라인)` 상태를 표시하며, 분석되지 않은 댓글을 임의로 `정상`으로 확정하지 않습니다.

---

## 3. 시스템 아키텍처 및 데이터 흐름

```text
[YouTube DOM (Chrome)] 
   │ 1. MutationObserver: 새 댓글 감지 (텍스트, URL, video_id, comment_id 추출 / 중복 요청 방지)
   ▼
[Chrome Extension (content.js / popup.js)]
   │ 2. HTTP POST /analyze (배치 또는 단건 댓글 페이로드 전송)
   ▼
[Local Python Stream Engine (src/server.py)]
   ├─► (Step A) URL 정규화 및 1차 Bloom Filter 조회 (Bit Array)
   │      └─► [양성(All 1s)일 때만] 디스크 원본 URL 목록(Index/File) 대조하여 오탐(FP) 제거
   ├─► (Step B) k-Shingling → MinHash 서명 → LSH 밴딩 후보 추출
   │      └─► 후보 쌍의 실제 Jaccard 유사도 재계산 + 홍보/연락처 유도 규칙 신호 결합
   ├─► (Step C) 최종 판정 도출: [SPAM_SUSPECT | NORMAL | HOLD] + 판별 근거(Reason) 생성
   └─► (Step D) 스트림 스케치 갱신 (현재 영상 기준)
          ├─► DGIM: 스팸 의심(1) / 그 외(0) 비트 스트림 입력 → 최근 N개 관측 댓글 내 의심 수 추정
          └─► Flajolet-Martin: 의심 댓글 내 고유 URL 수 및 고유 계정 식별값(확보 시) 수 추정
   │
   ▼ 3. 판정 결과 및 실시간 스트림 통계 JSON 반환
[Chrome Extension UI]
   ├─► 댓글 DOM: [🚨 악성 URL / ⚠️ 도배 의심] 배지 부착, 본문 접기 및 [원문 보기] 토글 버튼 렌더링
   └─► 팝업(Popup): 현재 영상 관측 댓글 수, 의심/보류 건수, DGIM·FM 추정치, 백엔드 연결 상태 표시
```

---

## 4. 핵심 알고리즘 명세 (구현 요구사항)

### 4.1 Bloom Filter와 2단계 악성 URL 조회 (`src/bloom_filter.py`)
* **이론적 설계:** 집합 크기 $m$, 비트 배열 크기 $n$, 해시 함수 개수 $k$에 대해 이론적 오탐률 공식 $p \approx \left(1 - e^{-km/n}\right)^k$ 및 최적 해시 개수 $k = \frac{n}{m}\ln 2$를 사용해 파라미터를 설정합니다.
* **단방향 오차 활용:**
  * **음성(하나라도 0):** 악성 URL 목록에 **절대 없음(False Negative = 0)**이 보장되므로 즉시 통과시킵니다.
  * **양성(모두 1):** 실제 악성 URL이거나 거짓 양성(False Positive)이므로, 메모리에 전체 목록을 중복 보관하지 않고 **디스크의 정렬된 원본 파일(또는 오프셋 인덱스)에서 2차 정확 조회**를 수행해 최종 확정합니다.
* **URL 정규화:** 조회 전 프로토콜(`http/https`), 대소문자, 끝 슬래시(`/ tracking param`) 등을 일관되게 정규화합니다.

### 4.2 Shingling · MinHash · LSH 유사 댓글 탐지 (`src/lsh_detector.py`)
* **표현 및 서명:** 댓글 공백을 정규화한 뒤 문자 단위 $k$-shingle 집합(기본 $k=5$, 한국어·영어 혼용 고려 조정)으로 변환하고, $n$개의 해시 치환($h_i(x) = (a_i x + b_i) \bmod p$)으로 길이 $n$의 MinHash 시그니처를 생성합니다.
* **LSH 밴딩 및 2차 검증:** 시그니처를 $b$개 밴드 $\times$ $r$개 행($b \times r = n$, 문턱값 $t \approx (1/b)^{1/r}$)으로 나누어 하나 이상의 밴드에서 같은 버킷에 해싱된 쌍만 후보로 추출합니다. 후보 쌍은 반드시 **원래 shingle 집합의 실제 Jaccard 유사도**로 재확인합니다.
* **예외 및 메모리 상한 관리:**
  * 짧은 감탄사("ㅋㅋㅋ", "Good", "1등")나 정상적인 반복 반응이 스팸으로 오분류되지 않도록 **최소 글자 수 제한**을 두고, **홍보·외부 연락처(텔레그램, 카톡, 단축 URL 등) 유도 규칙 신호**와 결합하여 판별합니다.
  * LSH 인덱스는 **현재 영상의 최근 $W$개 댓글 + 고정된 스팸 참조 집합**으로 크기를 제한하여 메모리가 무한히 증가하는 것을 방지합니다.

### 4.3 DGIM 및 Flajolet-Martin 스트림 통계 (`src/sketches.py`)
* **DGIM (`DGIMCounter`):**
  * 관측된 댓글의 판정이 `SPAM_SUSPECT`이면 `1`, 그 외(`NORMAL`, `HOLD`)이면 `0`으로 변환해 입력합니다(`HOLD` 건수는 별도 카운터로도 표시).
  * 최근 $N$개 관측 댓글 윈도우를 $O(\log N)$개의 2의 거듭제곱 크기 버킷($O(\log^2 N)$ 비트 공간)으로 유지하며, 가장 오래된 버킷 크기의 절반을 더해 최대 50% 오차 이내로 추정합니다.
* **Flajolet-Martin (`FlajoletMartin`):**
  * 현재 영상의 스팸 의심 댓글에서 추출된 **고유 URL 수**와(안정적인 채널/작성자 ID를 DOM에서 확보 가능한 경우) **고유 계정 수**를 해시값 이진 표현의 최대 꼬리 0 개수($R$, 추정치 $2^R$)로 추정합니다.
  * 여러 해시 함수를 그룹화하여(평균과 중앙값 결합) 분산을 줄이고 실제 고유 집합(`set`) 크기와 오차를 비교합니다.
* **해석상 주의사항 (보고서 및 UI 명시):** 확장 프로그램의 댓글 관측 순서는 사용자의 스크롤에 따른 DOM 로딩 순서이므로 실제 작성 시각 순과 다를 수 있습니다. 따라서 최근 $N$개 관측 댓글 통계를 '실제 시간대별 공격 빈도'로 과장 해석하지 않고 **'현재 영상의 관측 스트림 기준 통계'**로 명시합니다.

---

## 5. 활용 데이터셋 및 엄격한 평가 분할 계획

데이터 유출(Data Leakage)과 성능 과대평가를 막기 위해 아래 기준에 따라 데이터를 분리하고 평가합니다.

| 구분 | 데이터셋 명칭 | 구성 및 평가 유의사항 |
| :--- | :--- | :--- |
| **악성 URL** | **URLhaus / PhishTank** | 수집 시점, 라이선스, 정규화·중복 제거 후 실제 건수를 `data/SOURCE.md`에 기록합니다. **목록에 있는 URL(회원 질의)**과 **목록에 없는 정상 URL(비회원 질의)**을 별도로 구성해 오탐률을 실측합니다. |
| **라벨 댓글** | **UCI YouTube Spam Collection** | 약 2,000건의 실제 댓글 데이터입니다. **① 참조(Reference)용 / ② 임계값 조정(Validation)용 / ③ 최종 평가(Test)용**으로 엄격히 분할하되, **동일하거나 매우 유사한 댓글이 분할 양쪽에 섞이지 않도록(Near-duplicate 그룹 단위 분할)** 처리합니다. |
| **보조 수집** | **YouTube Data API v3** | 허용된 쿼터 범위 내에서 오프라인 재생 실험용 댓글을 수집하는 보조 경로로 사용합니다(개인 식별자는 마스킹). |
| **제어 실험** | **검증용 변형 댓글 및 재생 스트림** | 문구 변형 비율과 도배 빈도를 인위적으로 제어한 합성/재생 스트림을 별도 구성하여, 실제 데이터 결과와 구분해 LSH 확장성 및 DGIM·FM 추정 오차를 정밀 검증합니다. (가능한 경우 한국어 스팸/정상 소표본 검증을 추가합니다.) |

---

## 6. 성능 측정 지표 (Benchmark Metrics)

`benchmarks/run_benchmarks.py` 실행 시 아래 5개 영역의 지표를 측정하여 `output/metrics.json`과 차트(`output/charts/*.png`)로 저장해야 합니다. (**목록 조회의 오탐률**과 **전체 댓글 분류의 오탐률**은 반드시 구분하여 보고합니다.)

1. **Bloom Filter:** 일반 파이썬 `set` 대비 메모리 사용량(MB) 및 조회 시간, 이론 오탐률 vs 실측 오탐률 곡선, 디스크 2차 정확 조회를 포함한 전체 지연 시간(Latency).
2. **MinHash & LSH:** $O(N^2)$ 전수 비교 대비 후보 쌍 축소율(Reduction Ratio) 및 처리 시간 교차점, 전수 비교 정답 쌍 대비 유사 쌍 탐색 재현율(Recall).
3. **전체 댓글 판별 파이프라인:** 정밀도(Precision), 재현율(Recall), F1-score, 정상 댓글 오탐률(FPR), 판단 보류(`HOLD`) 비율.
4. **DGIM & Flajolet-Martin:** 정확한 슬라이딩 윈도우/집합 계산 대비 상대 오차율(%) 및 메모리 사용량 비교.
5. **크롬 확장 프로그램 E2E:** 댓글 DOM 감지부터 화면 배지 표시까지의 **중앙값(p50) 및 상위 95퍼센트(p95) 지연 시간**, 스크롤 시 중복 처리 방지 여부, 서버 연결 실패 시 예외 동작 확인.

---

## 7. 디렉터리 구조 (Directory Structure)

```text
youtube-stream-spam-filter/
├── README.md                   # 프로젝트 설계 및 에이전트 구현 지침 (본 문서)
├── AGENTS.md                   # 코딩 에이전트 상시 준수 규칙
├── data/                       # 원본 데이터셋 보관 (수정 금지)
│   ├── SOURCE.md               # 데이터 출처, 수집일, 라이선스, 정제 후 건수 기록
│   ├── urlhaus_phishtank/      # 악성 URL 원본 및 디스크 조회용 정렬 파일
│   ├── uci_spam/               # UCI YouTube Spam Collection 원본 및 분할 인덱스
│   └── synthetic_stream/       # 제어 실험용 변형 댓글 및 재생 스트림
├── src/                        # 로컬 Python 스트림 분석 엔진 (순수 Python 3)
│   ├── __init__.py
│   ├── normalizer.py           # URL 및 댓글 텍스트 정규화
│   ├── bloom_filter.py         # Bloom Filter + 디스크 2단계 정확 조회기
│   ├── lsh_detector.py         # k-Shingling, MinHash, LSH 밴딩 및 규칙 결합기
│   ├── sketches.py             # DGIM 슬라이딩 윈도우 & Flajolet-Martin 추정기
│   ├── pipeline.py             # 통합 스팸 판별 파이프라인 (SPAM_SUSPECT / NORMAL / HOLD)
│   └── server.py               # 확장 프로그램 통신용 로컬 HTTP API 서버 (127.0.0.1:8000)
├── extension/                  # PC Chrome 확장 프로그램 (Manifest V3)
│   ├── manifest.json           # 권한 및 Content Script 설정
│   ├── content.js              # 댓글 DOM 감지, 중복 방지, 접기/원문 보기 배지 렌더링
│   ├── styles.css              # 스팸 마스킹(Blur/Fold) 및 배지 스타일
│   ├── popup.html              # 실시간 스트림 통계 및 서버 상태 팝업 UI
│   └── popup.js                # 백엔드 통계 폴링 및 토글 제어
├── benchmarks/                 # 성능 측정 및 시각화 스크립트
│   ├── run_benchmarks.py       # 메모리·오탐률·지연시간·추정오차 벤치마크 실행
│   └── plot_charts.py          # 결과 비교 차트 생성 (output/charts/)
├── output/                     # 실험 결과 수치(JSON/CSV), 차트(PNG), 최종 보고서(MD/PDF)
└── test_tasks.py               # 핵심 알고리즘 이론치/정확도 자동 검증 스크립트
```

---

## 8. 로컬 실행 및 검증 절차 (Quick Start)

### Step 1. 알고리즘 단위 및 이론 검증 (`test_tasks.py`)
외부 패키지 없이 파이썬 3 표준 환경에서 즉시 실행되어 모든 알고리즘의 정합성을 검증합니다.
```bash
python3 test_tasks.py
```
* **검증 항목:** ① Bloom Filter 거짓 음성(FN) = 0% 및 이론 오탐률 오차 범위 확인, ② 교재 예제 기반 MinHash/LSH 후보 추출 재현율 확인, ③ DGIM 오차 50% 이내 보장 확인, ④ Flajolet-Martin 고유 개수 추정치 확인.

### Step 2. 로컬 Python 스트림 서버 실행
```bash
python3 -m src.server --host 127.0.0.1 --port 8000
```
* **주요 엔드포인트:**
  * `GET /health` : 서버 상태 및 로드된 필터 파라미터 반환
  * `POST /analyze` : 댓글 리스트 입력 $\rightarrow$ 댓글별 `status` (`SPAM_SUSPECT` | `NORMAL` | `HOLD`), `reason`, `latency_ms` 및 현재 영상의 DGIM/FM 통계 반환
  * `POST /reset` : 영상 이동(`video_id` 변경) 시 해당 영상의 스트림 상태 초기화

### Step 3. 크롬 확장 프로그램 로드
1. PC Chrome 브라우저에서 `chrome://extensions/` 접속
2. 우측 상단 **개발자 모드(Developer mode)** 활성화
3. **압축해제된 확장 프로그램을 로드합니다(Load unpacked)** 클릭 후 본 저장소의 `extension/` 폴더 선택
4. 유튜브 일반 동영상 페이지에 접속하여 댓글 스크롤 시 실시간 배지 표시, 댓글 접기/원문 보기 동작, 확장 프로그램 팝업 통계 확인

---

## 9. 주차별 구현 마일스톤 (10주차 ~ 15주차, 12/8 마감)

| 주차 | 핵심 구현 목표 및 완료 기준 (Definition of Done) |
| :--- | :--- |
| **10주차** | • 주제·지원 범위 확정 및 데이터 사용 조건(`data/SOURCE.md`) 기록<br>• URLhaus/PhishTank 및 UCI 데이터셋 확보, 유사 댓글 중복 유출을 방지한 학습/조정/평가 분할 스크립트 작성 |
| **11주차** | • `src/bloom_filter.py` 구현(2단계 디스크 재확인 포함) 및 일반 해시셋(`set`) 대비 메모리·속도·오탐률 비교 실험<br>• 크롬 확장 프로그램 최소 동작 연결: 댓글 추출(`content.js`) $\rightarrow$ 로컬 서버(`server.py`) $\rightarrow$ 화면 배지 표시 |
| **12주차** | • `src/lsh_detector.py` 구현($k$-shingling, MinHash, LSH 밴딩, Jaccard 재검증 및 홍보성 신호 결합)<br>• 짧은 댓글 예외 처리, 인덱스 크기 상한 적용 및 백엔드 연결 실패 시 보류/오프라인 안전 처리 구현 |
| **13주차** | • UCI 라벨 데이터 및 변형 재생 스트림 기반 전체 판별 성능 평가와 임계값 확정<br>• `src/sketches.py` (DGIM, Flajolet-Martin) 구현 및 정확한 슬라이딩 윈도우/집합 계산 대비 오차·메모리 비교 |
| **14주차** | • 확장 프로그램 UI 완성(의심 댓글 자동 접기, `[원문 보기]` 토글, 팝업 스트림 통계 대시보드)<br>• E2E 지연 시간(p50, p95) 측정, 벤치마크 차트 생성(`output/charts/`) 및 최종 보고서 초안 작성 |
| **15주차** | • 전체 실행 절차 및 재현성(`test_tasks.py`, `run_benchmarks.py`) 최종 검수<br>• 최종 보고서, 크롬 확장 프로그램 패키지, 로컬 서버 코드 및 시연 자료 제출 (**12월 8일 마감**) |
