import re
import threading
import time
from collections import OrderedDict, deque
from .algorithms import DGIM, FlajoletMartin, MinHashLSH, extract_urls, shingles
from .solar import SolarAgent


def heuristic(text, urls, matches):
    compact = re.sub(r'\s+', '', text).casefold()
    contact = bool(re.search(r'텔레그램|카카오톡|카톡|오픈채팅|telegram|whatsapp|t\.me/', compact))
    finance = bool(re.search(r'고수익|수익보장|원금보장|투자|부업|수익인증|수익공개|돈벌|수익률|guaranteedprofit', compact))
    action = bool(re.search(r'프로필|링크|연락|문의|입장|참여|클릭|profile|click|join', compact))
    credential = bool(re.search(r'비밀번호|계정인증|지갑연결|password|verifyyouraccount', compact))
    if any(m['verdict'] == 'spam' and m['similarity'] >= 0.72 for m in matches):
        return 'spam', '이미 스팸 의심으로 판정한 댓글과 문구가 매우 유사합니다.'
    if contact and finance and action:
        return 'spam', '외부 연락처 유도와 투자·수익 홍보 표현이 함께 있습니다.'
    if credential and urls and action:
        return 'spam', '외부 링크와 계정·인증정보 입력 유도 표현이 함께 있습니다.'
    profile_link = bool(re.search(r'프로필.*링크|profile.*link', compact))
    if finance and action or contact and action or urls and action or profile_link:
        return 'review', '홍보 또는 외부 이동을 유도하는 표현이 있어 추가 검토가 필요합니다.'
    return 'safe', '현재 규칙에서 스팸 근거를 찾지 못했습니다. 안전을 보장하는 판정은 아닙니다.'


class Session:
    def __init__(self):
        self.results = OrderedDict()
        self.index = MinHashLSH()
        self.dgim = DGIM()
        self.fm_urls, self.fm_accounts = FlajoletMartin(), FlajoletMartin()
        self.total = self.spam = self.review = self.agent_calls = 0
        self.account_observations = 0
        self.history = deque(maxlen=60)
        self.started = self.touched = time.monotonic()

    def stats(self):
        return {'total': self.total, 'spam': self.spam, 'review': self.review,
                'safe': self.total - self.spam - self.review,
                'window': min(self.total, self.dgim.window),
                'recent_spam_estimate': round(self.dgim.estimate(), 1),
                'unique_spam_urls_estimate': self.fm_urls.estimate(),
                'unique_spam_accounts_estimate': self.fm_accounts.estimate() if self.account_observations else None,
                'agent_calls': self.agent_calls, 'lsh_records': len(self.index.records),
                'lsh_comparisons': self.index.comparisons, 'history': list(self.history)}


class Engine:
    def __init__(self, blocklist, solar=None, max_sessions=32):
        self.blocklist = blocklist
        self.solar = solar or SolarAgent()
        self.sessions = OrderedDict()
        self.max_sessions = max_sessions
        self.lock = threading.RLock()
        self.agent_times = deque()

    def analyze(self, session_id, comments, allow_solar=False):
        with self.lock:
            session = self.sessions.setdefault(session_id, Session())
            self.sessions.move_to_end(session_id)
            while len(self.sessions) > self.max_sessions:
                self.sessions.popitem(last=False)
            session.touched = time.monotonic()
            results, agent_used = [], False
            for comment in comments:
                key = comment['id']
                if key in session.results:
                    results.append(session.results[key])
                    continue
                start = time.perf_counter()
                text = comment['text'][:2000]
                urls = extract_urls(text, comment.get('urls', []))
                lookups = [{'url': u, **self.blocklist.lookup(u)} for u in urls]
                elements = shingles(text)
                similar, signature = session.index.query(elements)
                verdict, reason = heuristic(text, urls, similar)
                source = 'rules'
                if any(item['matched'] for item in lookups):
                    verdict, reason, source = 'spam', '등록된 악성 URL 또는 도메인과 정확히 일치합니다.', 'blocklist'
                agent = {'status': 'not_needed', 'calls': 0}
                if verdict == 'review':
                    agent['status'] = 'disabled'
                    now = time.monotonic()
                    while self.agent_times and now - self.agent_times[0] > 60:
                        self.agent_times.popleft()
                    if allow_solar and self.solar.enabled:
                        agent['status'] = 'budget_limited'
                        if not agent_used and session.agent_calls <= 18 and len(self.agent_times) < 6:
                            agent_used = True
                            self.agent_times.append(now)
                            decision, agent = self.solar.review(text, urls, lookups, similar)
                            session.agent_calls += agent['calls']
                            if decision:
                                verdict, reason, source = decision['verdict'], decision['reason'], 'solar'
                session.index.add(key, elements, signature, text, verdict)
                session.total += 1
                session.spam += verdict == 'spam'
                session.review += verdict == 'review'
                session.dgim.add(verdict == 'spam')
                if verdict == 'spam':
                    for url in urls:
                        session.fm_urls.add(url)
                    if comment.get('account'):
                        session.fm_accounts.add(comment['account'])
                        session.account_observations += 1
                session.history.append(1 if verdict == 'spam' else 0)
                result = {'id': key, 'verdict': verdict, 'reason': reason, 'source': source,
                          'url_matches': sum(item['matched'] for item in lookups),
                          'similar_count': len(similar), 'agent': agent,
                          'latency_ms': round((time.perf_counter() - start) * 1000, 2)}
                session.results[key] = result
                while len(session.results) > 3000:
                    session.results.popitem(last=False)
                results.append(result)
            return {'results': results, 'stats': session.stats(), 'blocklist_count': self.blocklist.count,
                    'solar_available': self.solar.enabled}
