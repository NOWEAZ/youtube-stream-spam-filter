import copy
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch
from backend.blocklist import Blocklist
from backend.engine import Engine
from backend.opencode import OpenCodeAgent
from backend.profiles import avatar_url, load_avatar, name_signals, NoRedirect, MAX_IMAGE_BYTES
from backend.server import validate

IMAGE_URL = 'https://yt3.ggpht.com/profile-test=s48'
IMAGE_DATA = 'data:image/png;base64,iVBORw0KGgo='


def final(verdict='spam'):
    return {'choices': [{'finish_reason': 'stop', 'message': {
        'content': json.dumps({'verdict': verdict, 'reason': '프로필 이미지의 연락처 유도 문구'})}}]}


class ProfileTest(unittest.TestCase):
    def test_advertising_names_are_hints_not_unusual_identity_names(self):
        for name in ('텔레그램 문의주세요', '프로필 링크 확인', '성인사이트 안내', 'Free coins contact me', 'https://promo.test'):
            self.assertTrue(name_signals(name))
        for name in ('김민수', 'ユーザー123', 'أحمد', 'user983201', '🎵 음악 좋아요', '张伟'):
            self.assertEqual(name_signals(name), [])

    def test_avatar_source_allowlist_and_query_removal(self):
        self.assertEqual(avatar_url(IMAGE_URL+'?private=token#id'), IMAGE_URL)
        for raw in ('http://yt3.ggpht.com/x', 'https://yt3.ggpht.com.evil.test/x',
                    'https://127.0.0.1/x', 'https://yt3.ggpht.com:8080/x',
                    'https://user:pass@yt3.ggpht.com/x', 'file:///test', 'data:image/png;base64,x'):
            self.assertIsNone(avatar_url(raw))
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'http://127.0.0.1/'))

    def test_thumbnail_is_bounded_and_download_sends_no_key_or_cookie(self):
        headers = Message()
        headers['Content-Type'] = 'image/png'
        with patch('backend.profiles.urllib.request.build_opener') as builder:
            response = builder.return_value.open.return_value.__enter__.return_value
            response.headers = headers
            response.read.return_value = b'\x89PNG\r\n\x1a\nsmall-image'
            self.assertTrue(load_avatar(IMAGE_URL).startswith('data:image/png;base64,'))
            request = builder.return_value.open.call_args.args[0]
            self.assertIsNone(request.get_header('Authorization'))
            self.assertIsNone(request.get_header('Cookie'))
            headers['Content-Length'] = str(MAX_IMAGE_BYTES + 1)
            with self.assertRaises(ValueError):
                load_avatar(IMAGE_URL)
            del headers['Content-Length']
            response.read.return_value = b'<svg>untrusted</svg>'
            with self.assertRaises(ValueError):
                load_avatar(IMAGE_URL)

    def test_multimodal_request_contains_image_and_generic_signals_not_name(self):
        calls = []
        loader = Mock(return_value=IMAGE_DATA)
        agent = OpenCodeAgent(True, lambda body, _: calls.append(copy.deepcopy(body)) or final(), loader)
        result, trace = agent.review('감사합니다', [], [], [],
                                    author_signals=['name_contact_promotion'], profile_image_url=IMAGE_URL)
        self.assertEqual(result['verdict'], 'spam')
        self.assertTrue(trace['image_attached'])
        content = calls[0]['messages'][1]['content']
        self.assertEqual(content[1]['image_url']['url'], IMAGE_DATA)
        self.assertEqual(json.loads(content[0]['text'])['author_signals'], ['name_contact_promotion'])
        loader.assert_called_once_with(IMAGE_URL, 5)
        agent = OpenCodeAgent(True, lambda *_: final(), Mock(side_effect=ValueError('private image exception')))
        result, trace = agent.review('감사합니다', [], [], [], profile_image_url=IMAGE_URL)
        self.assertIsNone(result)
        self.assertEqual(trace, {'status': 'image_unavailable', 'calls': 0})

    def test_server_validates_profile_fields_and_consent_types(self):
        payload = {'session': 'profiles', 'comments': [{'id': '1', 'text': 'hello'}]}
        for extra in ({'author_name': 7}, {'author_avatar': 'a'*2049}):
            with self.assertRaises(ValueError):
                validate({**payload, 'comments': [{**payload['comments'][0], **extra}]})
        with self.assertRaises(ValueError):
            validate({**payload, 'allow_profile_images': 'true'})


class ProfileEngineTest(unittest.TestCase):
    def test_local_names_and_image_opt_in_preserve_non_hiding_policy(self):
        root = Path(__file__).resolve().parents[1]/'.local/test-temp'
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            block = Blocklist(Path(directory)/'empty.db')
            calls, loader = [], Mock(return_value=IMAGE_DATA)
            agent = OpenCodeAgent(True, lambda body, _: calls.append(body) or final(), loader)
            engine = Engine(block, agent)
            try:
                comment = {'id': '1', 'text': '좋은 영상 감사합니다', 'author_name': '텔레그램 문의', 'author_avatar': IMAGE_URL}
                result = engine.analyze('local-only', [comment], False, True)['results'][0]
                self.assertEqual(result['verdict'], 'review')
                self.assertEqual(result['source'], 'author_profile')
                self.assertEqual(calls, [])
                ordinary = {**comment, 'author_name': 'user983201'}
                self.assertEqual(engine.analyze('images-off', [ordinary], True, False)['results'][0]['verdict'], 'safe')
                loader.assert_not_called()
                result = engine.analyze('images-on', [ordinary], True, True)['results'][0]
                self.assertEqual(result['verdict'], 'review')
                self.assertEqual(result['source'], 'opencode')
                self.assertTrue(result['agent']['image_attached'])
                self.assertEqual(engine.sessions['images-on'].index.records['1']['verdict'], 'safe')
                loader.assert_called_once()
                self.assertNotIn('author_name', json.loads(calls[0]['messages'][1]['content'][0]['text']))
                agent.transport = lambda *_: final('review')
                result = engine.analyze('unclear-image', [ordinary], True, True)['results'][0]
                self.assertEqual(result['verdict'], 'safe')
                agent.image_loader = Mock(side_effect=TimeoutError())
                result = engine.analyze('failed-image', [ordinary], True, True)['results'][0]
                self.assertEqual(result['verdict'], 'safe')
                self.assertEqual(result['agent']['calls'], 0)
            finally:
                block.db.close()
