import json
import tempfile
import unittest
from pathlib import Path
from backend.labeling import DraftLabeler, LabelStore, PROMPT_HASH, redact_text
from backend.opencode import OpenCodeAgent


def response(label='spam', finish='stop', reason='외부 사이트 홍보입니다.'):
    return {'choices': [{'finish_reason': finish, 'message': {
        'content': json.dumps({'label': label, 'reason': reason})}}]}


class LabelingTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]/'.local/test-temp'
        self.root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.root)
        self.store = LabelStore(Path(self.temp.name)/'labels.db')
        self.bodies = []
        self.agent = OpenCodeAgent(True, lambda body, timeout: self.bodies.append(body) or response())

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_masks_contact_url_secrets_and_html_without_losing_host(self):
        text = '<a href="https://person:password@t.me/private-user?token=secret">Join us</a> a@example.com 010-1234-5678 @private_user'
        redacted = redact_text(text)
        self.assertIn('https://t.me/[PATH]', redacted)
        self.assertIn('Join us', redacted)
        for private in ('password', 'secret', 'private-user', 'private_user', 'a@example.com', '010-1234-5678'):
            self.assertNotIn(private, redacted)
        self.assertEqual(redact_text(redacted), redacted)

    def test_independent_draft_prompt_and_strict_label_schema(self):
        result, error = DraftLabeler(self.agent).classify('Please subscribe to my channel')
        self.assertEqual(result['label'], 'spam')
        self.assertIsNone(error)
        body = self.bodies[0]
        self.assertEqual(json.loads(body['messages'][1]['content']), {'comment': 'Please subscribe to my channel'})
        self.assertNotIn('tools', body)
        self.assertEqual(len(self.bodies), 1)

    def test_rejects_invalid_truncated_empty_and_tool_responses(self):
        cases = [response('safe'), response('spam', 'length'), response(reason=''),
                 {'choices': [{'message': {'tool_calls': [{'function': {'name': 'execute_code'}}]}}]}]
        for payload in cases:
            with self.subTest(payload=payload):
                result, error = DraftLabeler(OpenCodeAgent(True, lambda *_: payload)).classify('comment')
                self.assertIsNone(result)
                self.assertIsNotNone(error)

    def test_failure_does_not_become_a_normal_label_or_leak_credentials(self):
        def fail(*_):
            raise RuntimeError('private-credential-in-exception')
        result, error = DraftLabeler(OpenCodeAgent(True, fail)).classify('comment')
        sample_id = self.store.add({'text': 'hello world'}, self.agent)
        self.store.save(sample_id, self.agent, result, error)
        path = Path(self.temp.name)/'export.jsonl'
        self.store.export(path)
        item = json.loads(path.read_text(encoding='utf-8'))
        self.assertIsNone(item['ai_label'])
        self.assertEqual(item['status'], 'error')
        self.assertNotIn('private-credential', path.read_text(encoding='utf-8'))
        self.assertEqual(len(self.store.pending(self.agent, 10)), 0)
        self.assertEqual(len(self.store.pending(self.agent, 10, retry_errors=True)), 1)

    def test_dedup_resume_and_unreviewed_provenance(self):
        record = {'text': '영화 Casino 재밌네요', 'source': 'test', 'source_id': 'raw-private-id',
                  'AUTHOR': 'private-name', 'CLASS': 1, 'human_label': 'spam'}
        sample_id = self.store.add(record, self.agent)
        self.assertEqual(self.store.add(record, self.agent), sample_id)
        self.assertEqual(len(self.store.pending(self.agent, 10)), 1)
        self.store.save(sample_id, self.agent, {'label': 'normal', 'reason': '영화 감상입니다.'}, None)
        self.assertEqual(len(self.store.pending(self.agent, 10)), 0)
        path = Path(self.temp.name)/'export.jsonl'
        self.store.export(path)
        item = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(item['label_source'], 'ai')
        self.assertEqual(item['review_status'], 'unreviewed')
        self.assertIsNone(item['human_label'])
        self.assertFalse(item['eligible_for_gold_evaluation'])
        self.assertEqual(item['prompt_hash'], PROMPT_HASH)
        self.assertNotIn('AUTHOR', item)
        self.assertNotIn('CLASS', item)
        self.assertNotEqual(item['source_id'], 'raw-private-id')

    def test_changed_model_has_separate_annotation_history(self):
        sample = {'text': 'hello world'}
        sample_id = self.store.add(sample, self.agent)
        self.store.save(sample_id, self.agent, {'label': 'normal', 'reason': '감상'}, None)
        self.agent.model = 'different-model'
        self.store.add(sample, self.agent)
        self.assertEqual(len(self.store.pending(self.agent, 10)), 1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM annotations').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
