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
                     '텔 레 그 램으로 문의하세요! guaranteed profit!'):
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

    def test_user_reported_criticism_is_not_an_advertisement(self):
        comments = [
            '유튜브 측에서 막을 수 있었으면 진작에 막았습니다. 못 막아요. 왜냐면 처음엔 일반 댓글처럼 써놓고 조회수 작업으로 베플 만들어놓고 이상태로는 유튜브 댓글 규정 위반이 아니기 때문에 못 막아요. 또 유튜브에 가장 큰 헛점인 댓글 수정 기능을 통해 나중엔 성인 사이트 유도 하기 때문에 원래는 다른 일반 댓글이였지만 시간 지나면 댓글 다른걸로 바껴있어서 낚시 당하는 사람들이 많죠 이걸 노리는거거든요. 애초에 유튜브가 저런걸 막으려면 원댓글 수정 기능을 없애는 게 유일한 차단 방법임.',
            '근데 성인사이트 홍보댓글뿐 아니라 도박사이트 홍보하는 댓글도 보여서 그게 더 열받는다는점이예요',
            '텔레그램 투자 사기는 조심하세요',
            'Beware of guaranteed profit scams on Telegram.',
        ]
        for text in comments:
            with self.subTest(text=text):
                self.assertEqual(classify(text, [], [])[0], 'safe')

    def test_single_keywords_and_self_promotion_cannot_hide_comments(self):
        for text in ('카지노 드라마 재밌네요', '토토 노래 너무 좋아요',
                     '제 채널에서 쓰는 카메라도 이 모델입니다', '구독 부탁하는 장면이 웃기네요',
                     '카톡으로 투자 이야기를 나눴어요', 'I love the movie Casino.',
                     'This is my new video editing setup.', 'Please subscribe to my channel!',
                     'Please do not subscribe to my channel just for a giveaway.'):
            with self.subTest(text=text):
                self.assertNotEqual(classify(text, [], [])[0], 'spam')

    def test_similarity_alone_never_upgrades_to_automatic_hiding(self):
        matches = [{'verdict': 'spam', 'similarity': .99}]
        verdict, _, rules = classify('제 채널에서 쓰는 카메라와 같은 모델이라 반갑습니다', [], matches)
        self.assertEqual(verdict, 'review')
        self.assertFalse(rules['strong_evidence'])

    def test_concrete_solicitation_still_detected_without_warning_whitelist(self):
        comments = [
            ('카지노 신규 가입하세요! 꽁머니 지급 https://casino.test', ['https://casino.test']),
            ('인증번호를 보내주세요! https://steal.test', ['https://steal.test']),
            ('사기 아닙니다. 원금 보장! 텔레그램으로 문의하세요', []),
        ]
        for text, urls in comments:
            with self.subTest(text=text):
                self.assertEqual(classify(text, urls, [])[0], 'spam')
        # A warning word added to a concrete offer cannot make it "safe".
        self.assertEqual(classify('투자 사기 조심하세요. 원금 보장! 텔레그램 문의하세요', [], [])[0], 'review')

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
