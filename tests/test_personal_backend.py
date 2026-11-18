import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock
from backend.blocklist import Blocklist
from backend.engine import Engine
from backend.opencode import OpenCodeAgent
from backend.server import handler_factory, validate


def preference(label='normal', kind='exact'):
    result = {'label': label, 'kind': kind, 'reference_id': 'a'*64}
    if kind == 'similar':
        result['reference_label'] = 'normal'
    return result


class PersonalBackendTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]/'.local/test-temp'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.block = Blocklist(Path(self.temp.name)/'empty.db')
        self.agent = OpenCodeAgent(True, Mock(side_effect=AssertionError('No AI for personal labels')))
        self.engine = Engine(self.block, self.agent)

    def tearDown(self):
        self.block.db.close()
        self.temp.cleanup()

    def test_same_comment_can_have_opposite_user_preferences_without_ai(self):
        comment = {'id': 'same', 'text': '내가 보는 댓글입니다'}
        spam = self.engine.analyze('profile-a', [{**comment, 'personal_match': preference('spam')}], True)['results'][0]
        normal = self.engine.analyze('profile-b', [{**comment, 'personal_match': preference('normal')}], True)['results'][0]
        self.assertEqual(spam['verdict'], 'spam')
        self.assertEqual(normal['verdict'], 'safe')
        self.assertEqual(normal['source'], 'personal_exact')
        self.assertEqual(self.engine.sessions['profile-a'].index.records['same']['verdict'], 'safe')
        self.agent.transport.assert_not_called()

    def test_normal_correction_survives_a_strong_rule(self):
        comment = {'id': '1', 'text': '원금 보장! 텔레그램 문의하세요', 'personal_match': preference()}
        result = self.engine.analyze('corrected', [comment], True)['results'][0]
        self.assertEqual(result['verdict'], 'safe')
        self.assertEqual(result['agent']['calls'], 0)

    def test_near_normal_is_only_review_and_near_spam_does_not_weaken_strong_rules(self):
        text = '원금 보장! 텔레그램 문의하세요'
        near_normal = preference('review', 'similar')
        result = self.engine.analyze('near-normal', [{'id': '1', 'text': text, 'personal_match': near_normal}], True)['results'][0]
        self.assertEqual(result['verdict'], 'review')
        near_spam = {**near_normal, 'reference_label': 'spam'}
        result = self.engine.analyze('near-spam', [{'id': '1', 'text': text, 'personal_match': near_spam}], False)['results'][0]
        self.assertEqual(result['verdict'], 'spam')

    def test_invalid_match_metadata_is_rejected(self):
        for row in ({'label': 'normal'}, preference('review'), {**preference(), 'reference_id': 'bad'}):
            with self.assertRaises(ValueError):
                validate({'session': 'test', 'comments': [{'id': '1', 'text': 'hello', 'personal_match': row}]})

    def test_http_never_enables_automatic_images_even_with_old_consent(self):
        engine = Mock()
        engine.analyze.return_value = {'results': [], 'stats': {}}
        server = ThreadingHTTPServer(('127.0.0.1', 0), lambda *args: None)
        port = server.server_address[1]
        server.RequestHandlerClass = handler_factory(engine, 'test-token', port)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            body = {'session': 'test', 'comments': [{'id': '1', 'text': 'hello'}], 'allow_opencode': True, 'allow_profile_images': True}
            request = urllib.request.Request(f'http://127.0.0.1:{port}/api/analyze', data=json.dumps(body).encode(),
                headers={'Content-Type': 'application/json', 'X-YCSF-Token': 'test-token'})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=3) as response:
                self.assertEqual(response.status, 200)
            self.assertFalse(engine.analyze.call_args.args[3])
        finally:
            server.shutdown(); server.server_close(); thread.join()
