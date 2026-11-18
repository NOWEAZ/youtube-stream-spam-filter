"""Profile promotion hints and bounded thumbnail loading; never identify a person."""
import base64
import re
import urllib.request
from urllib.parse import urlsplit, urlunsplit
from .rules import normalize_text

AVATAR_HOSTS = {'yt3.ggpht.com', 'yt3.googleusercontent.com'}
MAX_IMAGE_BYTES = 128_000


def name_signals(name):
    text = normalize_text(name)
    compact = re.sub(r'\s+', '', text)
    signals = []
    if re.search(r'https?://|www\.|\bt\.me/', text):
        signals.append('name_url_promotion')
    if re.search(r'(?:카톡|텔레그램|디엠).*(?:문의|연락|아이디)', compact) or re.search(
            r'\b(?:telegram|whatsapp|dm me|contact me)\b|t\.me/', text):
        signals.append('name_contact_promotion')
    if re.search(r'성인사이트|무료코인|수익보장|원금보장|꽁머니|카지노가입', compact) or re.search(
            r'\b(?:free (?:coins|robux)|guaranteed profit|casino bonus)\b', text):
        signals.append('name_advertising_phrase')
    if re.search(r'(?:프로필|소개란).*(?:링크|클릭)', compact) or re.search(
            r'\b(?:link in (?:bio|profile)|check my profile)\b', text):
        signals.append('name_profile_link_promotion')
    return signals


def avatar_url(raw):
    if not isinstance(raw, str) or not raw or len(raw) > 2048:
        return None
    try:
        parsed = urlsplit(raw)
        if (parsed.scheme != 'https' or parsed.hostname not in AVATAR_HOSTS
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.path in ('', '/')):
            return None
        return urlunsplit(('https', parsed.hostname, parsed.path, '', ''))
    except ValueError:
        return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def load_avatar(raw, timeout=5):
    url = avatar_url(raw)
    if not url:
        raise ValueError('unsupported avatar source')
    # Never send credentials, cookies or an API Authorization header to the image host.
    request = urllib.request.Request(url, headers={'User-Agent': 'youtube-stream-spam-filter/0.5.0'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=min(timeout, 5)) as response:
        if int(response.headers.get('Content-Length', '0')) > MAX_IMAGE_BYTES:
            raise ValueError('avatar too large')
        data = response.read(MAX_IMAGE_BYTES + 1)
        mime = response.headers.get_content_type()
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError('avatar too large')
    valid = ((mime == 'image/png' and data.startswith(b'\x89PNG\r\n\x1a\n'))
             or (mime == 'image/jpeg' and data.startswith(b'\xff\xd8\xff'))
             or (mime == 'image/webp' and data.startswith(b'RIFF') and data[8:12] == b'WEBP'))
    if not valid:
        raise ValueError('unsupported avatar image')
    return 'data:' + mime + ';base64,' + base64.b64encode(data).decode('ascii')
