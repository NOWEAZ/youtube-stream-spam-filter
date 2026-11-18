# 댓글 가드

빅데이터개론 기획서를 구현한 **크롬 확장 프로그램과 로컬 Python 분석 서버**입니다. PC 유튜브 일반 동영상의 로드된 댓글을 분석하고, 스팸 의심 근거·원문 보기·정상 표시 해제 기능을 제공합니다. 실제 댓글을 삭제하거나 신고하지 않습니다.

## 빠른 시작

### 1 서버 실행

이 폴더에서 Python 3.10 이상으로 실행합니다. 서버는 Python 표준 라이브러리만 사용하므로 `pip install`이 필요하지 않습니다.

```powershell
python -m backend.server --demo-data
```

Windows에서 Python 명령이 설정되어 있지 않으면 제공한 스크립트를 사용할 수 있습니다. 이 컴퓨터의 Codex 번들 Python도 자동으로 찾습니다.

```powershell
powershell -ExecutionPolicy Bypass -File .\start-server.ps1 -DemoData
```

현재 실행에만 `ExecutionPolicy Bypass`를 적용하며, 시스템 설정을 변경하지 않습니다. 터미널을 열어 둔 상태로 사용하고 종료할 때 Ctrl+C를 누르세요.

서버가 출력하는 `Connection token` 값을 복사합니다. 토큰은 `.local/connection-token.txt`에도 저장되며 재실행해도 유지됩니다. `.local` 폴더는 공유하거나 제출하지 마세요.

`--demo-data`는 **실제 위협 정보가 아닌 예약된 .test 도메인 두 개**를 사용합니다. 이 상태에서도 연락처·홍보 규칙과 LSH 분석은 동작하지만 실제 악성 URL 탐지 범위가 확보된 것은 아닙니다.

### 2 크롬 확장 프로그램 설치

1. 크롬 주소창에 `chrome://extensions`를 입력합니다.
2. 오른쪽 위 **개발자 모드**를 켭니다.
3. **압축해제된 확장 프로그램을 로드합니다**를 누릅니다.
4. 이 프로젝트 안의 **`extension` 폴더**를 선택합니다. 프로젝트 전체나 ZIP 파일을 선택하지 마세요.
5. 크롬 확장 프로그램 메뉴에서 **댓글 가드**를 열고 **서버 연결 설정**에 토큰을 붙여 넣습니다. 기본 포트는 `8765`입니다.
6. **설정 저장 및 연결 확인**을 누르고, 유튜브 일반 동영상 페이지를 새로고침한 뒤 댓글 영역으로 스크롤합니다.

댓글을 더 불러오거나 답글을 펼치면 추가 분석합니다. 댓글 가드 팝업에서 자동 접기와 필터 사용을 끌 수 있습니다. 설정 저장 시 연결 확인은 서버 상태를 검사하고, 토큰의 유효성은 첫 댓글 분석에서 확인합니다.

### 3 실제 유튜브 없이 시연

[로컬 시연 화면](http://127.0.0.1:8765/demo)을 열고 동일한 토큰을 입력합니다. 확장 프로그램을 설치하지 않아도 샘플 댓글을 통해 동일한 `content.js`의 감지·분석·접기·복원 동작을 확인할 수 있습니다.

‘새 댓글 불러오기’, 직접 입력, 자동 접기 해제, ‘원문 보기’, ‘정상 댓글입니다’를 확인해 보세요. 시연 페이지의 **OpenCode 추가 검토 사용**은 기본적으로 꺼져 있습니다. 서버와 페이지 양쪽에서 OpenCode를 활성화하면 검토 후보를 실제 API로 보낼 수 있습니다. 이 시연은 실제 YouTube DOM과 Chrome 확장 런타임의 검증을 대신하지 않습니다.

## 제공 기능

| 구성 | 구현 |
|---|---|
| 댓글 감지 | MutationObserver, 최대 5개 배치, 대기열 상한, 댓글 ID 또는 텍스트 지문 중복 방지 |
| 악성 URL 검사 | Bloom Filter 후보 선별 후 SQLite 디스크 목록에서 정확 조회 |
| 유사 댓글 | 문자 3-shingle, MinHash 48개, LSH 16밴드 × 3행, Jaccard 재확인 |
| 추가 규칙 | 외부 연락처와 수익 홍보·행동 유도, 링크와 인증정보 요구 조합 |
| 화면 | 스팸 의심과 검토 필요 구분, 선택적 접기, 원문 복원, 영상 내 정상 표시 해제 |
| 통계 | 관측 건수·판정별 건수, 최근 60개 흐름, DGIM 최근 1,000개 의심 수, FM 고유 URL·계정 추정 |
| OpenCode | 허용된 정확 조회·유사 댓글 도구 선택과 호출, 최종 JSON 판정, 실패 시 검토 유지 |
| 실패 처리 | 서버 장애·토큰 오류 표시, 5초 간격 재시도, 원문 유지, 영상 이동 시 상태 초기화 |

`safe`는 ‘현재 근거 없음’이며 안전 보장이 아닙니다. `review`는 검토 필요로 표시하고 자동으로 접지 않습니다. ‘정상 댓글입니다’는 현재 영상에서 표시만 해제하며 모델 학습·유튜브 신고·서버 판정 통계 변경은 수행하지 않습니다.

## 악성 URL 목록 가져오기

자동 다운로드하지 않습니다. 이용 가능한 URLhaus·PhishTank 등의 자료를 사용 조건에 맞게 확보하고 아래 UTF-8 텍스트로 변환해 주세요. CSV·JSON 원본은 직접 지원하지 않습니다.

```text
# 전체 URL 정확 일치
https://example.test/path?code=A
# 도메인 전체를 차단하려는 경우에만 명시
domain:another-example.test
```

```powershell
python -m backend.server --blocklist .\data\my_urls.txt
```

정확 URL은 경로·쿼리의 대소문자를 유지하고, 호스트 대소문자·기본 포트·fragment를 정규화합니다. URL의 한 경로가 등록되었다고 해당 도메인 전체를 차단하지 않습니다. `domain:` 항목은 정확한 호스트만 일치하며 하위 도메인을 자동 포함하지 않습니다. 유튜브 `/redirect?q=...` 링크는 대상 URL로 정규화합니다. 링크를 방문하거나 파일을 내려받지 않습니다.

일반 목록은 `.local/threats.sqlite3`, 시연 목록은 `.local/demo-threats.sqlite3`에 분리됩니다. 가져오기는 누적 추가 방식이며 서버 재시작 때 Bloom Filter를 재구성합니다. 목록 삭제·만료·주기적 갱신 UI는 아직 없습니다. URL 축약 서비스의 최종 목적지, 동형 문자, 이미지에 포함된 URL은 해석하지 않습니다.

## OpenCode Zen·Go API 사용

OpenCode는 API 제공 서비스이며 모델 이름과는 구분됩니다. 기존 Z.ai 직접 연결을 OpenCode Console API 연결로 교체했습니다. 기본값은 **비활성**이며, 서버와 브라우저 양쪽에서 허용해야 후보 댓글을 보냅니다.

프로젝트 폴더의 PowerShell에서 실행하세요.

```powershell
$env:OPENCODE_API_KEY = '발급받은 OpenCode Console API 키'
$env:OPENCODE_PROVIDER = 'zen'
$env:OPENCODE_MODEL = 'kimi-k3'
powershell -ExecutionPolicy Bypass -File .\start-server.ps1 -DemoData -OpenCode
```

실제 URL 목록을 사용하려면 `-DemoData` 대신 `-Blocklist .\data\my_urls.txt`를 지정하세요. Python 직접 실행은 `python -m backend.server --opencode --blocklist .\data\my_urls.txt`입니다. 서버는 사용자가 직접 실행하고 `Ctrl+C`로 종료합니다.

서버 터미널의 `OpenCode: True`, 제공 방식과 모델 ID를 확인하세요. 확장 프로그램을 이미 설치했다면 `chrome://extensions`에서 다시 로드하고 유튜브 페이지를 새로고침하세요. 팝업에 **연결 토큰**을 입력하고 **OpenCode 추가 검토**를 직접 켜세요. 연결 토큰은 로컬 서버 인증용이며 OpenCode API 키와 다릅니다. 기존 GLM·Solar 전송 동의와 키는 자동으로 재사용하지 않습니다. 실행 옵션도 `--opencode`, `-OpenCode`로 바뀌었습니다.

시연은 `http://127.0.0.1:8765/demo`에서 연결 토큰을 입력하고 **OpenCode 추가 검토 사용**을 켜세요. ‘관련 자료는 프로필 링크에서 확인할 수 있어요’가 추가 검토 후보입니다. **OpenCode 호출** 횟수는 시도 횟수이며 성공을 보장하지 않습니다.

### 제공 방식과 모델 설정

현재 [공식 API 문서](https://opencode.ai/docs/console/api/inference/)와 [Base URLs 문서](https://opencode.ai/docs/console/harnesses#base-urls)에 맞춰 아래 주소를 사용합니다. 주소를 임의로 바꿔 키를 다른 서버에 보내는 설정은 지원하지 않습니다.

| 환경변수 | 값 | 의미 |
| --- | --- | --- |
| `OPENCODE_API_KEY` | Console에서 발급받은 키 | 서버에만 저장 |
| `OPENCODE_PROVIDER` | `zen` (기본) | 일반 종량제 Console API, `https://opencode.ai/inference/openai/v1/chat/completions` |
| `OPENCODE_PROVIDER` | `go` | Go 구독 API, `https://opencode.ai/inference/go/openai/v1/chat/completions` |
| `OPENCODE_MODEL` | `kimi-k3` (기본) | 계정에서 이용 가능한 도구 호출 지원 모델의 ID |

모델 ID에는 `opencode/`, `opencode-go/` 접두사를 붙이지 않습니다. 예를 들어 `glm-5.3`도 OpenCode를 통해 지정할 수 있습니다. 모델 변경 시 서버를 재시작하세요. 무료 모델은 외부 클라이언트에서 403으로 거부될 수 있으므로 무료 사용을 가정하지 않습니다.

**Go 선택 시:** `$env:OPENCODE_PROVIDER = 'go'`로 바꾸면 구독 API 주소를 사용합니다. 다만 [Go 공식 안내](https://opencode.ai/docs/console/go/#where-can-i-use-it)는 코딩 에이전트 요청을 대상으로 합니다. 이 댓글 분류 앱의 이용 가능 여부를 서비스 측에 확인하시고, 일반 댓글 분석에는 종량제 `zen` 설정을 사용하세요. Go 구독으로 이 앱의 사용을 보장하지 않습니다. 실제 클라이언트 이름과 검토 대화별 임의 세션 ID를 전송하며, 같은 도구 호출 대화의 두 요청은 같은 ID를 사용합니다.

`.env.example`은 예시이며 자동으로 읽지 않습니다. 키를 확장 프로그램 파일이나 Git에 넣지 마세요. 추가 검토 후보의 댓글 본문 최대 1,500자, 추출 URL, 도구로 조회한 유사 댓글 일부가 OpenCode 및 선택한 모델 제공자로 전송됩니다. 작성자 표시명과 원래 계정 주소는 보내지 않지만 댓글 본문 자체에는 개인정보가 있을 수 있습니다.

에이전트는 최대 2회 API 요청, 첫 응답에서 최대 2개 읽기 전용 도구 호출, 총 12초의 요청 대기 예산을 사용합니다. 배치당 1개 후보, 서버 전체 분당 6개 후보, 영상 세션당 최대 20회 API 요청으로 제한합니다. 잘린 응답·키 오류·예산 초과·잘못된 JSON·도구 근거 없는 응답은 **검토 필요**로 유지합니다. 이미 정확 조회로 일치한 악성 URL을 AI가 정상으로 뒤집지 않습니다. 모델 전용 `thinking` 옵션은 보내지 않습니다. OpenCode 옵션 변경 시 새 세션으로 재분석합니다.

키가 없는 환경에서는 모의 응답으로 요청 주소·인증·도구 호출·실패 처리를 검증하며, 실제 API 성공 여부와 모델 성능은 키 설정 후 확인해야 합니다.

## 테스트와 평가

```powershell
python -m unittest discover -s tests -v
node --test tests/test_background.cjs
python scripts/evaluate.py --size 10000
# 선택: 헤더가 text,label인 UTF-8 CSV. label은 0/1 또는 ham/spam
python scripts/evaluate.py --csv data/evaluation.csv
```

`reports/benchmark.json`에는 합성 데이터 여부, seed, Bloom Filter와 해시셋의 메모리·조회 시간·오탐률, LSH 후보 비교 횟수와 재현율, DGIM·FM 추정 오차를 기록합니다. Bloom Filter의 조회 시간이 해시셋보다 빠르다고 가정하지 않습니다. 기본 벤치마크는 필터 자체를 비교하며 SQLite 정확 조회·HTTP 지연은 포함하지 않습니다. 해시셋 메모리는 `getsizeof` 합계, Bloom 메모리는 비트 배열 크기이므로 전체 프로세스 RSS 비교는 별도로 수행해야 합니다.

CSV 평가는 기본 규칙과 입력 순서에 따라 쌓이는 LSH 인덱스를 평가하며 블랙리스트와 OpenCode는 사용하지 않습니다. 검토 필요는 양성으로 세지 않습니다. UCI의 `CONTENT,CLASS` 파일을 그대로 넣을 수 없으며 `text,label`로 변환해야 합니다. 실제 데이터 최종 성능, 한국어 라벨 검증, 실제 OpenCode 적용 전후 비교, 전체 지연 분포는 데이터와 키를 확보한 후 별도로 측정해야 합니다. 동일·준중복 댓글이 참조·최종 평가에 섞이지 않도록 분리해 주세요.

## 범위와 알려진 제약

- PC `www.youtube.com/watch`의 현재 DOM에 로드된 댓글과 답글을 대상으로 합니다. 라이브 채팅·Shorts·모바일·영상 전체 댓글 자동 수집은 지원하지 않습니다.
- 유튜브의 공개 DOM 선택자를 사용합니다. 화면 구조나 A/B 실험으로 선택자가 바뀌면 `content.js`의 댓글 선택자와 추출 코드를 수정해야 합니다.
- 계정은 링크를 확보한 경우에만 세션별 salt로 SHA-256 해시하여 로컬 서버에 전달합니다. 작성자 이름을 식별자로 사용하지 않습니다. 댓글의 텍스트 지문으로 중복을 제거할 때는 동일 작성자의 동일 문구가 합쳐질 수 있습니다.
- 사용자에게 보이는 시계열은 게시 시간순이 아닌 관측 순서입니다. DGIM은 스팸 의심을 1로 세고 검토 필요는 별도 집계합니다. 기본 DGIM의 오차 한계는 최대 약 50%이며, Python 정수 타임스탬프를 사용하므로 이론적인 비트 단위 공간과 실행 객체의 메모리는 다릅니다.
- FM은 강의의 최대 trailing-zero 추정과 그룹 평균·중앙값 결합을 사용합니다. 특히 작은 집합에서도 오차가 크며 URL·계정의 실제 개수와 다를 수 있습니다. 정확한 계정 수나 스팸 조직의 증거로 사용하지 마세요.
- 엔진은 최대 32개 세션, 세션당 LSH 500개 댓글·최근 결과 3,000개, 브라우저는 결과 2,000개·대기 300개를 유지합니다. 초과한 오래된 항목은 제거합니다. 제거 후 재등장하면 다시 관측될 수 있습니다. 긴 댓글의 유사도는 앞 1,500자로 제한합니다.
- 서버 분석은 현재 직렬화되어 있습니다. 한 사용자의 수업 시연을 대상으로 하며 다중 사용자 서비스나 성능이 검증된 배포판은 아닙니다.

## 파일 구성

```text
extension/      크롬에 로드할 Manifest V3 확장 프로그램
backend/        표준 라이브러리 HTTP 서버와 분석 알고리즘, OpenCode 에이전트
demo/           동일 콘텐츠 스크립트를 사용하는 로컬 샘플 화면
data/           시연용 .test URL 목록
tests/          알고리즘·HTTP·에이전트·서비스 워커 회귀 테스트
scripts/        벤치마크, 아이콘 생성, 패키징 도구
start-server.ps1 Windows 실행 스크립트
.local/         실행 시 생성되는 토큰·SQLite 데이터베이스 (공유 제외)
```

통신은 고정된 loopback 서버만 사용하며 연결 토큰과 Origin/Host 검증을 적용합니다. OpenCode를 켠 경우에만 서버가 OpenCode 공식 API에 접속합니다. 크롬의 [공식 확장 프로그램 네트워크 문서](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests)에 따라 content script의 네트워크 요청은 서비스 워커를 거칩니다.
