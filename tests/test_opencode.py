import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.blocklist import Blocklist
from backend.engine import Engine
from backend.opencode import OpenCodeAgent
from backend.server import validate


def tool_message(name='lookup_current_urls'):
    return {'choices': [{'finish_reason': 'tool_calls', 'message': {
        'content': None, 'reasoning_content': 'tool evidence', 'tool_calls': [
            {'id': 'call_1', 'type': 'function', 'function': {'name': name, 'arguments': '{}'}}]}}]}


def final_message(verdict='safe', finish='stop'):
    return {'choices': [{'finish_reason': finish, 'message': {
        'content': json.dumps({'verdict': verdict, 'reason': '조회 근거를 확인했습니다.'})}}]}


class OpenCodeTest(unittest.TestCase):
    def test_requires_new_key_and_explicit_server_enable(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(OpenCodeAgent(True).enabled)
        with patch.dict(os.environ, {'OPENCODE_API_KEY': 'test-only-key'}, clear=True):
            self.assertFalse(OpenCodeAgent(False).enabled)
            self.assertTrue(OpenCodeAgent(True).enabled)
            self.assertEqual(OpenCodeAgent().model, 'kimi-k3')

    def test_http_transport_uses_zen_endpoint_and_server_key(self):
        with patch.dict(os.environ, {'OPENCODE_API_KEY': 'test-only-key'}, clear=True):
            agent = OpenCodeAgent(True)
        with patch('backend.opencode.urllib.request.urlopen') as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(final_message()).encode()
            agent._request({'model': agent.model, 'messages': []}, 3)
            request = urlopen.call_args.args[0]
            self.assertEqual(request.full_url, 'https://opencode.ai/inference/openai/v1/chat/completions')
            self.assertEqual(request.get_header('Authorization'), 'Bearer test-only-key')
            self.assertEqual(urlopen.call_args.kwargs['timeout'], 3)
            self.assertNotIn('test-only-key', request.data.decode())

    def test_go_endpoint_and_session_headers_are_stable_for_tool_turns(self):
        with patch.dict(os.environ, {'OPENCODE_API_KEY': 'test-only-key', 'OPENCODE_PROVIDER': 'go'}, clear=True):
            agent = OpenCodeAgent(True)
        with patch('backend.opencode.urllib.request.urlopen') as urlopen:
            urlopen.return_value.__enter__.return_value.read.side_effect = [
                json.dumps(tool_message()).encode(), json.dumps(final_message()).encode()]
            result, trace = agent.review('자료는 프로필 링크를 보세요', [], [], [])
            self.assertIsNotNone(result)
            self.assertEqual(trace['calls'], 2)
            requests = [call.args[0] for call in urlopen.call_args_list]
            self.assertTrue(all(request.full_url ==
                'https://opencode.ai/inference/go/openai/v1/chat/completions' for request in requests))
            self.assertEqual(requests[0].get_header('X-opencode-session'), requests[1].get_header('X-opencode-session'))
            self.assertTrue(requests[0].get_header('X-opencode-session').startswith('ses_'))
            self.assertEqual(requests[0].get_header('User-agent'), 'youtube-stream-spam-filter/0.3.0')

    def test_old_key_cannot_enable_new_provider_and_invalid_config_fails(self):
        with patch.dict(os.environ, {'ZAI_API_KEY': 'old-key'}, clear=True):
            self.assertFalse(OpenCodeAgent(True).enabled)
        for values in ({'OPENCODE_PROVIDER': 'https://evil.test'},
                       {'OPENCODE_MODEL': ''}, {'OPENCODE_MODEL': 'opencode/kimi-k3'}):
            with patch.dict(os.environ, values, clear=True), self.assertRaises(ValueError):
                OpenCodeAgent(True)

    def test_two_turn_protocol_and_portable_parameters(self):
        calls = []
        def transport(body, timeout):
            calls.append(copy.deepcopy(body))
            self.assertLessEqual(timeout, 12)
            return tool_message() if len(calls) == 1 else final_message()
        agent = OpenCodeAgent(True, transport)
        result, trace = agent.review('자료는 프로필 링크를 보세요', [], [{'matched': False}], [])
        self.assertEqual(result['verdict'], 'safe')
        self.assertEqual(trace['tools'], ['lookup_current_urls'])
        self.assertNotIn('thinking', calls[0])
        self.assertFalse(calls[0]['stream'])
        self.assertEqual(calls[0]['tool_choice'], 'auto')
        self.assertNotIn('tools', calls[1])
        self.assertNotIn('tool_choice', calls[1])
        self.assertEqual(calls[1]['messages'][-1]['role'], 'tool')
        self.assertEqual(calls[1]['messages'][-2]['reasoning_content'], 'tool evidence')

    def test_other_model_does_not_receive_unsupported_thinking_disable(self):
        calls = []
        with patch.dict(os.environ, {'OPENCODE_MODEL': 'glm-5.3'}, clear=True):
            agent = OpenCodeAgent(True, lambda body, _: calls.append(copy.deepcopy(body)) or final_message())
        agent.review('예시', [], [], [])
        self.assertEqual(calls[0]['model'], 'glm-5.3')
        self.assertNotIn('thinking', calls[0])

    def test_truncated_response_unknown_tool_and_extra_turn_are_rejected(self):
        for response in (final_message('spam', 'length'), tool_message('execute_code')):
            agent = OpenCodeAgent(True, lambda *_: response)
            result, trace = agent.review('예시', [], [], [])
            self.assertIsNone(result)
            self.assertEqual(trace['status'], 'unavailable')
        agent = OpenCodeAgent(True, lambda *_: tool_message())
        result, trace = agent.review('예시', [], [], [])
        self.assertIsNone(result)
        self.assertEqual(trace['calls'], 2)

    def test_invalid_final_json_is_not_accepted(self):
        calls = []
        def transport(*_):
            calls.append(True)
            return tool_message() if len(calls) == 1 else {'choices': [{'message': {'content': 'not json'}}]}
        result, trace = OpenCodeAgent(True, transport).review('예시', [], [], [])
        self.assertIsNone(result)
        self.assertEqual(trace['status'], 'unavailable')

    def test_engine_requires_browser_opt_in_and_reports_opencode_result(self):
        root = Path(__file__).resolve().parents[1]/'.local/test-temp'
        root.mkdir(parents=True, exist_ok=True)
        calls = []
        def transport(*_):
            calls.append(True)
            return tool_message('find_similar_comments') if len(calls) % 2 else final_message()
        with tempfile.TemporaryDirectory(dir=root) as temp:
            blocklist = Blocklist(Path(temp)/'empty.db')
            try:
                engine = Engine(blocklist, OpenCodeAgent(True, transport))
                comment = [{'id': '1', 'text': '자료는 프로필 링크를 확인해 주세요'}]
                before = engine.analyze('off', comment, False)
                self.assertEqual(before['results'][0]['verdict'], 'review')
                self.assertEqual(calls, [])
                after = engine.analyze('on', comment, True)
                self.assertEqual(after['results'][0]['source'], 'opencode')
                self.assertEqual(after['stats']['agent_calls'], 2)
                self.assertTrue(after['opencode_available'])
            finally:
                blocklist.db.close()

    def test_api_requires_boolean_opencode_opt_in(self):
        payload = {'session': 'test', 'comments': [{'id': '1', 'text': 'hello'}]}
        self.assertFalse(validate(payload)[2])
        self.assertFalse(validate({**payload, 'allow_glm': True})[2])
        self.assertTrue(validate({**payload, 'allow_opencode': True})[2])
        with self.assertRaises(ValueError):
            validate({**payload, 'allow_opencode': 'true'})


if __name__ == '__main__':
    unittest.main()
