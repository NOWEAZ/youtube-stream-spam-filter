# AGENTS.md — 코딩 에이전트 상시 준수 규칙 (`youtube-stream-spam-filter`)

이 저장소에서 작업하는 모든 코딩 에이전트는 코드를 작성, 수정, 테스트하거나 문서를 갱신할 때 반드시 아래 규칙을 준수해야 합니다.

---

## 1. 핵심 알고리즘 및 구현 제약

1. **선택적 3차 LLM 검증 허용 (`HOLD` 경계선 댓글 한정):**
   * 1·2차 스트림 판별은 반드시 **빅데이터 스트림·유사도 알고리즘(`BloomFilter`, `Shingling`, `MinHash`, `LSH`, `DGIM`, `FlajoletMartin`)과 규칙 기반 신호 결합**으로 수행하며, 판단 보류(`HOLD`)로 분류된 댓글에 한해 **OpenCode API(`OPENCODE_API_KEY`) 기반 선택적 3차 검증**을 허용합니다.
2. **핵심 알고리즘 직접 구현 (Black-box 패키지 금지):**
   * `src/` 하위의 핵심 알고리즘 모듈(`bloom_filter.py`, `lsh_detector.py`, `sketches.py`, `normalizer.py`, `pipeline.py`, `server.py`)은 반드시 **Python 3 표준 라이브러리(`hashlib`, `math`, `random`, `collections`, `bisect`, `http.server` 등)만으로 직접 구현**해야 합니다.
   * `pybloom`, `datasketch`, `mmh3`, `bitarray`, `scikit-learn` 등 알고리즘이 내장된 외부 서드파티 패키지 import를 금지합니다.
3. **고정 시드 및 재현성 보장 (`SEED = 42`):**
   * 해시 치환 계수 생성, 데이터 분할, 합성 스트림 생성, 벤치마크 측정 등 모든 난수 연산에는 `SEED = 42`를 고정으로 사용합니다.
   * 보고서 및 `output/metrics.json`에 기록되는 모든 수치는 산출 스크립트(`benchmarks/run_benchmarks.py`, `test_tasks.py`)와 입력 데이터 경로/열(column)을 명확히 추적할 수 있어야 합니다.

---

## 2. 데이터 보호 및 윤리 수칙

1. **원본 데이터 수정 금지:**
   * `data/` 디렉터리 내의 원본 데이터 파일은 절대 직접 수정하거나 덮어쓰지 마십시오.
   * 데이터 수집 시점, 라이선스, 정규화·중복 제거 후 실제 건수는 `data/SOURCE.md`에 기록하며, 아직 확인되지 않은 수치는 임의로 추정해 적지 말고 반드시 `(확인 필요)`로 표기합니다.
2. **개인정보 및 윤리 준수:**
   * 작성자 표시명(Display Name)만으로 계정을 식별하지 마십시오(고유 채널/작성자 ID가 DOM에서 안정적으로 확보된 경우에만 해싱하여 스케치 입력으로 사용).
   * 불필요한 개인 식별 정보(PII)를 디스크에 평문으로 저장하지 마십시오.
3. **데이터 유출(Data Leakage) 방지:**
   * UCI YouTube Spam Collection 등 평가 데이터 분할 시, 동일하거나 매우 유사한 댓글이 Reference / Validation / Test 분할에 동시에 섞이지 않도록 Near-duplicate 그룹 단위로 분할해야 합니다.

---

## 3. 시스템 아키텍처 및 장애 격리 (Fail-safe)

1. **2단계 악성 URL 검증:**
   * `BloomFilter`가 음성(`False`)을 반환하면 즉시 정상 통과시킵니다(False Negative = 0%).
   * `BloomFilter`가 양성(`True`)을 반환하면 반드시 **디스크의 정렬된 원본 URL 파일에서 2차 이진 탐색(또는 오프셋 인덱스 조회)**을 수행해 False Positive를 제거해야 합니다.
2. **LSH 후보 검증 및 메모리 상한:**
   * LSH 밴딩으로 추출된 후보 쌍은 반드시 **원래 $k$-shingle 집합의 실제 Jaccard 유사도**로 재확인합니다.
   * 짧은 감탄사("ㅋㅋㅋ", "Good", "1등" 등)가 스팸으로 오탐되지 않도록 최소 글자 수 기준과 홍보성/외부 연락처 유도 신호를 결합합니다.
   * LSH 인덱스는 현재 영상의 최근 $W$개 댓글 + 고정 참조 스팸 집합으로 크기를 제한합니다.
3. **장애 격리 및 판정 보류(`HOLD`):**
   * 확신이 낮거나 경계선상에 있는 댓글은 `HOLD`(`판단 보류`) 상태로 분류합니다.
   * 로컬 Python 서버(`127.0.0.1:8000`)와 연결이 실패하거나 타임아웃이 발생하면 크롬 확장 프로그램은 `분석 불가(오프라인)` 상태를 표시해야 하며, 분석되지 않은 댓글을 임의로 `정상(NORMAL)`으로 확정하지 않습니다.
4. **스트림 통계 해석 주의:**
   * DGIM 및 Flajolet-Martin 통계는 사용자의 스크롤에 따른 **현재 영상의 관측 스트림 기준 통계**임을 UI와 보고서에 명시해야 합니다.
