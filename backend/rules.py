"""Korean/English signals adapted from Chang's standalone core; scores are not probabilities."""
import re
import unicodedata
from urllib.parse import urlsplit, parse_qs


def normalize_text(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    return re.sub(r'\s+', ' ', re.sub(r'[\u200b-\u200f\u202a-\u202e\u2060\ufeff]', '', text)).strip()


def language_of(text):
    ko = bool(re.search(r'[가-힣ㄱ-ㅎㅏ-ㅣ]', text))
    en = bool(re.search(r'[a-zA-Z]', re.sub(r'https?://\S+', '', text)))
    return 'mixed' if ko and en else 'ko' if ko else 'en' if en else 'other'


# English phrases use word boundaries; Korean also checks compact forms for spacing evasion.
SIGNALS = {
    'contact': (1.5, r'텔레그램|텔그|카카오톡|카톡|오픈채팅|위챗|디엠',
                r'\b(?:telegram|whatsapp|wechat|discord|dm me|message me|contact me|text me)\b', '외부 연락처 유도'),
    'money': (1.0, r'고수익|수익보장|원금보장|부업|수익인증|수익공개|리딩방|코인|투자|돈벌|수익률',
              r'\b(?:make money|earn \$?\d+|passive income|crypto|bitcoin|forex|investment|guaranteed (?:profit|returns?)|get rich|work from home)\b', '수익·투자 홍보'),
    'cta': (.75, r'클릭|문의|입장|참여|연락|신청|가입|방문|프로필링크',
            r'\b(?:click|join|contact|visit|sign ?up|register|apply now|check (?:it )?out|link in (?:bio|profile))\b', '행동 유도'),
    'profile': (1.5, r'프로필.{0,6}(?:링크|확인|클릭)|소개란.{0,6}링크',
                r'\b(?:link (?:in|on) (?:my )?(?:bio|profile)|(?:my|the) profile link|check my (?:bio|profile))\b', '프로필 링크 유도'),
    'self_promo': (3.0, r'맞구독|선구독|맞팔|내채널|제채널|구독.{0,4}(?:부탁|해주)',
                   r'\b(?:check out (?:my|our)|subscribe (?:to )?(?:my|me|our)|sub ?4 ?sub|(?:plz|please|pls) (?:sub|subscribe|follow)|my (?:new )?(?:channel|video|mixtape)|free (?:followers|robux|gift ?cards?))\b', '채널·상품 홍보'),
    'phishing': (2.0, r'비밀번호|계정인증|계정확인|계정정지|지갑연결|개인정보입력|당첨|경품',
                 r"\b(?:password|verify your (?:account|identity)|account (?:suspended|locked|verification)|seed phrase|claim your (?:prize|reward)|you(?:'ve| have)? won)\b", '계정·인증정보·경품 사칭'),
    'gambling': (3.0, r'토토|카지노|바카라|꽁머니|성인사이트|야동',
                 r'\b(?:casino|betting|sportsbook|jackpot|porn|xxx|onlyfans|escort|nsfw)\b', '도박·성인 광고'),
}


def relevant_urls(urls, video=''):
    """Internal timestamp/mention/hashtag links are navigation, not promotional evidence."""
    out = []
    for url in urls:
        parsed = urlsplit(url)
        host = parsed.hostname.removeprefix('www.') if parsed.hostname else ''
        if host in ('youtube.com', 'm.youtube.com', 'youtu.be'):
            target = parsed.path[1:] if host == 'youtu.be' else parse_qs(parsed.query).get('v', [''])[0]
            if (parsed.path.startswith(('/hashtag/', '/@', '/channel/'))
                    or (video and target == video) or (parsed.path == '/' and not target)):
                continue
        out.append(url)
    return out


def score_rules(text, urls=()):
    norm = normalize_text(text)
    compact = re.sub(r'[\s.\-_*·~,|/\\()\[\]]+', '', norm)
    hits = {}
    for name, (weight, ko, en, label) in SIGNALS.items():
        if re.search(ko, compact) or re.search(en, norm):
            hits[name] = (weight, label)
    external = [u for u in urls if (urlsplit(u).hostname or '').removeprefix('www.') not in ('youtube.com', 'youtu.be', 'm.youtube.com')]
    if external:
        hits['link'] = (.5, '외부 링크 포함')
    hosts = {(urlsplit(u).hostname or '').removeprefix('www.') for u in external}
    if hosts & {'bit.ly', 'tinyurl.com', 'han.gl', 'me2.do', 't.co', 'linktr.ee'}:
        hits['shortener'] = (1.5, '단축 URL·링크 모음')
    if hosts & {'t.me', 'open.kakao.com', 'wa.me', 'discord.gg'}:
        hits['messenger'] = (2.0, '메신저 초대 링크')
    combo = 0
    if 'contact' in hits and 'money' in hits:
        combo += 1.5
    if 'contact' in hits and 'cta' in hits:
        combo += .5
    if 'money' in hits and 'cta' in hits:
        combo += .5
    if 'phishing' in hits and external:
        combo += 1.0
    score = sum(weight for weight, _ in hits.values()) + combo
    reasons = [label for _, label in sorted(hits.values(), reverse=True)]
    return {'score': round(score, 2), 'signals': list(hits), 'reasons': reasons}


def classify(text, urls, matches):
    rules = score_rules(text, urls)
    # Similarity cannot create its own future spam evidence. Short reactions are excluded.
    similar = len(normalize_text(text)) >= 20 and any(
        m['verdict'] == 'spam' and m['similarity'] >= .72 for m in matches)
    if rules['score'] >= 3 or similar and rules['score'] >= .75:
        verdict = 'spam'
    elif rules['score'] >= 1.5 or urls and 'cta' in rules['signals']:
        verdict = 'review'
    else:
        verdict = 'safe'
    reason = ' · '.join(rules['reasons'][:3]) if verdict != 'safe' else '현재 근거 없음 · 안전을 보장하는 판정은 아닙니다.'
    if similar and verdict == 'spam':
        reason = '독립 근거로 판정한 스팸과 유사 · ' + reason
    return verdict, reason, rules
