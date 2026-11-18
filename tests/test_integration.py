import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.config import load_env
from backend.rules import classify, language_of, normalize_text, relevant_urls
from backend.algorithms import FlajoletMartin


class BilingualRulesTest(unittest.TestCase):
    def test_korean_english_and_mixed_spam(self):
        for text in ('투자 수익보장 텔레그램 문의하세요',
                     'Guaranteed profit! Join our Telegram and make money today.',
                     'Please subscribe to my channel!',
                     '텔 레 그 램으로 문의! guaranteed profit!'):
            with self.subTest(text=text):
                self.assertEqual(classify(text, [], [])[0], 'spam')

    def test_normal_english_and_short_reactions_are_not_spam(self):
        for text in ('I joined the class yesterday. This was helpful!',
                     'The joint performance was amazing.', 'Good', 'ㅋㅋㅋ',
                     'Thanks for the investment lecture.', '설명 감사합니다.'):
            with self.subTest(text=text):
                self.assertEqual(classify(text, [], [])[0], 'safe')

    def test_both_languages_can_be_held_for_opencode(self):
        for text in ('자료는 프로필 링크를 확인해 주세요', 'The link in my profile has the resources.'):
            self.assertEqual(classify(text, [], [])[0], 'review')

    def test_unicode_evasion_and_language_counts(self):
        self.assertEqual(normalize_text('ＴＥＬＥＧＲＡＭ\u200b'), 'telegram')
        for text, language in [('강의 감사합니다', 'ko'), ('Great video', 'en'),
                               ('좋은 video', 'mixed'), ('123 😊', 'other')]:
            self.assertEqual(language_of(text), language)

    def test_internal_links_ignored_but_external_video_retained(self):
        urls = ['https://www.youtube.com/watch?v=own&t=10', 'https://www.youtube.com/hashtag/music',
                'https://www.youtube.com/@author', 'https://www.youtube.com/watch?v=other',
                'https://external.test/info']
        self.assertEqual(relevant_urls(urls, 'own'), urls[3:])

    def test_similarity_needs_independent_promotion_and_minimum_length(self):
        matches = [{'verdict': 'spam', 'similarity': .99}]
        self.assertEqual(classify('This is a normal response to the music video.', [], matches)[0], 'safe')
        self.assertEqual(classify('Good', [], matches)[0], 'safe')


class ConfigAndSketchTest(unittest.TestCase):
    def test_local_key_config_is_allowlisted_and_process_takes_precedence(self):
        root = Path(__file__).resolve().parents[1]/'.local/test-temp'
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            file = Path(temp)/'.env'
            file.write_text("OPENCODE_API_KEY='dummy-local-key'\nOPENCODE_MODEL=local-model\nPATH=invalid\n", encoding='utf-8')
            with patch.dict(os.environ, {'OPENCODE_MODEL': 'process-model'}, clear=True):
                load_env(file)
                self.assertEqual(os.environ['OPENCODE_API_KEY'], 'dummy-local-key')
                self.assertEqual(os.environ['OPENCODE_MODEL'], 'process-model')
                self.assertNotIn('PATH', os.environ)

    def test_pcsa_accuracy_and_fixed_memory(self):
        for count in (10, 100, 1000, 10000):
            sketch = FlajoletMartin()
            for value in range(count):
                sketch.add(str(value))
            self.assertLess(abs(sketch.estimate() - count) / count, .35)
            self.assertEqual(len(sketch.maps), 64)


if __name__ == '__main__':
    unittest.main()
