import json
import random
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer
from backend.algorithms import BloomFilter, DGIM, FlajoletMartin, MinHashLSH, normalize_url, shingles
from backend.blocklist import Blocklist
from backend.engine import Engine
from backend.server import handler_factory, validate
from backend.solar import SolarAgent


class AlgorithmsTest(unittest.TestCase):
    def test_bloom_members_and_empirical_error(self):
        bloom = BloomFilter(1000, 0.01)
        for i in range(1000): bloom.add(f'member-{i}')
        self.assertTrue(all(f'member-{i}' in bloom for i in range(1000)))
        fp = sum(f'other-{i}' in bloom for i in range(10000)) / 10000
        self.assertLess(fp, 0.03)
        self.assertLess(abs(fp - bloom.predicted_error), 0.02)

    def test_url_normalization_preserves_path_query(self):
        self.assertEqual(normalize_url('HTTPS://Example.COM:443/Pay?Code=A#section'), 'https://example.com/Pay?Code=A')
        self.assertEqual(normalize_url('https://www.youtube.com/redirect?q=https%3A%2F%2Fevil.test%2Fa'), 'https://evil.test/a')
        self.assertIsNone(normalize_url('javascript:alert(1)'))
        self.assertIsNone(normalize_url('https://example.com:bad/x'))

    def test_lsh_reranking_and_eviction(self):
        index = MinHashLSH(limit=2)
        text = '투자 수익 인증 프로필 링크 텔레그램 문의하세요'
        elements = shingles(text)
        matches, signature = index.query(elements)
        self.assertEqual(matches, [])
        index.add('1', elements, signature, text, 'spam')
        matches, _ = index.query(shingles(text + '!'))
        self.assertTrue(matches)
        self.assertGreater(matches[0]['similarity'], 0.9)
        for i in range(2, 5):
            es = shingles('서로 다른 샘플 ' + str(i))
            _, sig = index.query(es)
            index.add(str(i), es, sig, str(i), 'safe')
        self.assertEqual(len(index.records), 2)
        self.assertNotIn('1', set().union(*index.buckets.values()))

    def test_dgim_sliding_window_bound(self):
        rng = random.Random(17)
        for probability in (0.05, 0.5, 0.95):
            window, values = DGIM(128), []
            for _ in range(2000):
                bit = int(rng.random() < probability)
                values.append(bit); window.add(bit)
                actual = sum(values[-128:])
                self.assertLessEqual(abs(window.estimate() - actual), max(0.5 * actual, 1))
                counts = [b[0] for b in window.buckets]
                self.assertTrue(all(counts.count(size) <= 2 for size in set(counts)))

    def test_fm_duplicate_invariance(self):
        fm = FlajoletMartin()
        self.assertEqual(fm.estimate(), 0)
        for i in range(100): fm.add(str(i))
        estimate = fm.estimate()
        for _ in range(3):
            for i in range(100): fm.add(str(i))
        self.assertEqual(fm.estimate(), estimate)
        self.assertGreater(estimate, 0)


class EngineTest(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[1]/'.local/test-temp'
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.block = Blocklist(Path(self.temp.name)/'threats.db')
        file = Path(self.temp.name)/'urls.txt'
        file.write_text('domain:phishing.test\nhttps://bad.test/Pay?Code=A\n', encoding='utf-8')
        self.block.import_file(file)
        self.engine = Engine(self.block)

    def tearDown(self):
        self.block.db.close(); self.temp.cleanup()

    def analyze(self, text, item_id='1'):
        return self.engine.analyze('session', [{'id': item_id, 'text': text}])

    def test_exact_lookup_and_bloom_false_positive_recheck(self):
        self.block.bloom.bits[:] = b'\xff' * len(self.block.bloom.bits)
        self.assertTrue(self.block.lookup('https://clean.test/')['bloom_positive'])
        self.assertFalse(self.block.lookup('https://clean.test/')['matched'])
        self.assertTrue(self.block.lookup('https://phishing.test/login')['matched'])
        self.assertFalse(self.block.lookup('https://sub.phishing.test/login')['matched'])
        self.assertFalse(self.block.lookup('https://bad.test/pay?Code=A')['matched'])

    def test_blacklist_dedup_and_missing_list_is_not_safety(self):
        result = self.analyze('https://phishing.test/login')
        self.assertEqual(result['results'][0]['verdict'], 'spam')
        self.assertEqual(self.analyze('https://phishing.test/login')['stats']['total'], 1)
        self.assertEqual(self.analyze('설명 감사합니다', '2')['results'][0]['verdict'], 'safe')

    def test_normal_duplicate_is_not_spam(self):
        self.analyze('노래 너무 좋아요 매일 듣고 있어요', '1')
        result = self.analyze('노래 너무 좋아요 매일 듣고 있어요!', '2')
        self.assertEqual(result['results'][0]['verdict'], 'safe')
        self.assertGreaterEqual(result['results'][0]['similar_count'], 1)

    def test_review_without_key_and_investment_contact(self):
        result = self.analyze('자료는 프로필 링크에서 확인해 보세요')
        self.assertEqual(result['results'][0]['verdict'], 'review')
        self.assertEqual(result['results'][0]['agent']['status'], 'disabled')
        result = self.analyze('투자 수익인증 프로필 링크 텔레그램 문의하세요', '2')
        self.assertEqual(result['results'][0]['verdict'], 'spam')

    def test_solar_executes_only_allowlisted_tools(self):
        calls = []
        def transport(body, timeout):
            calls.append(body)
            if len(calls) == 1:
                return {'choices': [{'message': {'content': None, 'tool_calls': [
                    {'id': 'call1', 'type': 'function', 'function': {'name': 'find_similar_comments', 'arguments': '{}'}}]}}]}
            return {'choices': [{'message': {'content': json.dumps({'verdict': 'review', 'reason': '근거가 부족합니다.'})}}]}
        agent = SolarAgent(True, transport)
        result, trace = agent.review('예시', [], [], [])
        self.assertEqual(result['verdict'], 'review')
        self.assertEqual(trace['calls'], 2)
        self.assertEqual(calls[1]['messages'][-1]['role'], 'tool')
        self.assertEqual(calls[1]['tool_choice'], 'none')
        bad = SolarAgent(True, lambda *_: {'choices': [{'message': {'content': '{"verdict":"spam","reason":"추측"}'}}]})
        self.assertIsNone(bad.review('예시', [], [], [])[0])
        outage = SolarAgent(True, lambda *_: (_ for _ in ()).throw(TimeoutError()))
        self.assertEqual(outage.review('예시', [], [], [])[1]['status'], 'unavailable')

    def test_validation_rejects_oversized_batches(self):
        with self.assertRaises(ValueError): validate({'session': 'ok', 'comments': [{'id': '1', 'text': 'ok'}] * 6})
        with self.assertRaises(ValueError): validate({'session': 'ok', 'comments': [{'id': '1', 'text': 5}]})

    def test_http_auth_origin_and_end_to_end(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), lambda *args: None)
        port = server.server_address[1]
        server.RequestHandlerClass = handler_factory(self.engine, 'test-pairing-token-1234567890', port)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = f'http://127.0.0.1:{port}'
        payload = json.dumps({'session': 'http', 'comments': [{'id': '1', 'text': 'https://phishing.test/'}]}).encode()
        def request(token='', origin=None):
            headers = {'Content-Type': 'application/json', 'X-YCSF-Token': token}
            if origin: headers['Origin'] = origin
            return urllib.request.urlopen(urllib.request.Request(url+'/api/analyze', data=payload, headers=headers), timeout=3)
        try:
            with self.assertRaises(urllib.error.HTTPError) as exc: request()
            self.assertEqual(exc.exception.code, 401)
            with self.assertRaises(urllib.error.HTTPError) as exc: request('test-pairing-token-1234567890', 'https://evil.test')
            self.assertEqual(exc.exception.code, 403)
            with request('test-pairing-token-1234567890', 'chrome-extension://'+'a'*32) as response:
                result = json.load(response)
            self.assertEqual(result['results'][0]['verdict'], 'spam')
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
