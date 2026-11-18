import re
import threading
import time
from collections import OrderedDict, deque
from .algorithms import DGIM, FlajoletMartin, MinHashLSH, extract_urls, shingles
from .opencode import OpenCodeAgent
from .rules import classify, language_of, relevant_urls


def heuristic(text, urls, matches):
    verdict, reason, _ = classify(text, urls, matches)
    return verdict, reason


class Session:
    def __init__(self):
        self.results = OrderedDict()
        self.index = MinHashLSH()
        self.dgim = DGIM()
        self.fm_urls, self.fm_accounts = FlajoletMartin(), FlajoletMartin()
        self.total = self.spam = self.review = self.agent_calls = 0
        self.account_observations = 0
        self.history = deque(maxlen=60)
        self.languages = dict(ko=0, en=0, mixed=0, other=0)
        self.started = self.touched = time.monotonic()

    def stats(self):
        return {'total': self.total, 'spam': self.spam, 'review': self.review,
                'safe': self.total - self.spam - self.review,
                'window': min(self.total, self.dgim.window),
                'recent_spam_estimate': round(self.dgim.estimate(), 1),
                'unique_spam_urls_estimate': self.fm_urls.estimate(),
                'unique_spam_accounts_estimate': self.fm_accounts.estimate() if self.account_observations else None,
                'agent_calls': self.agent_calls, 'languages': dict(self.languages), 'lsh_records': len(self.index.records),
                'lsh_comparisons': self.index.comparisons, 'history': list(self.history)}


class Engine:
    def __init__(self, blocklist, opencode=None, max_sessions=32):
        self.blocklist = blocklist
        self.opencode = opencode or OpenCodeAgent()
        self.sessions = OrderedDict()
        self.max_sessions = max_sessions
        self.lock = threading.RLock()
        self.agent_times = deque()

    def analyze(self, session_id, comments, allow_opencode=False):
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
                urls = relevant_urls(extract_urls(text, comment.get('urls', [])), comment.get('video', ''))
                lookups = [{'url': u, **self.blocklist.lookup(u)} for u in urls]
                elements = shingles(text)
                similar, signature = session.index.query(elements)
                verdict, reason, rule = classify(text, urls, similar)
                independent = rule['score'] >= 3
                source = 'rules'
                if any(item['matched'] for item in lookups):
                    verdict, reason, source = 'spam', '등록된 악성 URL 또는 도메인과 정확히 일치합니다.', 'blocklist'
                    independent = True
                agent = {'status': 'not_needed', 'calls': 0}
                if verdict == 'review':
                    agent['status'] = 'disabled'
                    now = time.monotonic()
                    while self.agent_times and now - self.agent_times[0] > 60:
                        self.agent_times.popleft()
                    if allow_opencode and self.opencode.enabled:
                        agent['status'] = 'budget_limited'
                        if not agent_used and session.agent_calls <= 18 and len(self.agent_times) < 6:
                            agent_used = True
                            self.agent_times.append(now)
                            decision, agent = self.opencode.review(text, urls, lookups, similar)
                            session.agent_calls += agent['calls']
                            if decision:
                                verdict, reason, source = decision['verdict'], decision['reason'], 'opencode'
                session.index.add(key, elements, signature, text, 'spam' if independent else 'safe')
                language = language_of(text)
                session.languages[language] += 1
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
                          'language': language, 'score': rule['score'], 'signals': rule['signals'],
                          'latency_ms': round((time.perf_counter() - start) * 1000, 2)}
                session.results[key] = result
                while len(session.results) > 3000:
                    session.results.popitem(last=False)
                results.append(result)
            return {'results': results, 'stats': session.stats(), 'blocklist_count': self.blocklist.count,
                    'opencode_available': self.opencode.enabled}
