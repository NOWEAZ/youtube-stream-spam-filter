"""Run: python -m backend.server --demo-data"""
import argparse
import hmac
import json
import os
import re
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from .blocklist import Blocklist
from .engine import Engine
from .solar import SolarAgent

ROOT = Path(__file__).resolve().parent.parent


def validate(payload):
    if not isinstance(payload, dict):
        raise ValueError('JSON 객체가 필요합니다.')
    session = payload.get('session')
    if not isinstance(session, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', session):
        raise ValueError('올바른 session 값이 필요합니다.')
    comments = payload.get('comments')
    if not isinstance(comments, list) or not 1 <= len(comments) <= 5:
        raise ValueError('댓글은 한 번에 1~5개까지 처리합니다.')
    for item in comments:
        if not isinstance(item, dict):
            raise ValueError('댓글 형식이 올바르지 않습니다.')
        for name, limit in [('id', 200), ('text', 2000)]:
            if not isinstance(item.get(name), str) or not 1 <= len(item[name]) <= limit:
                raise ValueError('댓글 id 또는 text 길이가 올바르지 않습니다.')
        urls = item.get('urls', [])
        if not isinstance(urls, list) or len(urls) > 20 or any(not isinstance(u, str) or len(u) > 2048 for u in urls):
            raise ValueError('URL 형식이 올바르지 않습니다.')
        if 'account' in item and (not isinstance(item['account'], str) or len(item['account']) > 128):
            raise ValueError('계정 해시 형식이 올바르지 않습니다.')
    if not isinstance(payload.get('allow_solar', False), bool):
        raise ValueError('allow_solar 값은 boolean이어야 합니다.')
    return session, comments, payload.get('allow_solar', False)


def handler_factory(engine, token, port, demo_data=False):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'CommentGuard/0.1'

        def log_message(self, *_):
            pass  # Do not log comment content or authentication headers.

        def origin_ok(self):
            origin = self.headers.get('Origin')
            return (origin is None or bool(re.fullmatch(r'chrome-extension://[a-p]{32}', origin))
                    or origin == f'http://127.0.0.1:{port}')

        def host_ok(self):
            return self.headers.get('Host') in (f'127.0.0.1:{port}', f'localhost:{port}')

        def send(self, code, content, mime='application/json; charset=utf-8'):
            if not isinstance(content, bytes):
                content = json.dumps(content, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
            if self.origin_ok() and self.headers.get('Origin'):
                self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
                self.send_header('Vary', 'Origin')
            self.end_headers()
            self.wfile.write(content)

        def do_OPTIONS(self):
            if not self.origin_ok() or not self.host_ok():
                return self.send(403, {'error': '허용되지 않은 출처입니다.'})
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', self.headers.get('Origin', f'http://127.0.0.1:{port}'))
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-YCSF-Token')
            self.end_headers()

        def do_GET(self):
            if not self.host_ok() or not self.origin_ok():
                return self.send(403, {'error': '허용되지 않은 출처입니다.'})
            path = urlsplit(self.path).path
            if path == '/api/health':
                return self.send(200, {'ok': True, 'version': '0.1.0', 'blocklist_count': engine.blocklist.count,
                    'demo_data': demo_data, 'solar_available': engine.solar.enabled, 'model': engine.solar.model})
            files = {'/': ROOT/'demo/index.html', '/demo': ROOT/'demo/index.html',
                     '/demo/': ROOT/'demo/index.html', '/demo/demo.js': ROOT/'demo/demo.js',
                     '/demo/demo.css': ROOT/'demo/demo.css',
                     '/assets/content.js': ROOT/'extension/content.js',
                     '/assets/content.css': ROOT/'extension/content.css'}
            file = files.get(path)
            if file and file.is_file():
                mime = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css'}[file.suffix]
                return self.send(200, file.read_bytes(), mime + '; charset=utf-8')
            return self.send(404, {'error': '찾을 수 없습니다.'})

        def do_POST(self):
            if not self.host_ok() or not self.origin_ok():
                return self.send(403, {'error': '허용되지 않은 출처입니다.'})
            if not hmac.compare_digest(self.headers.get('X-YCSF-Token', ''), token):
                return self.send(401, {'error': '연결 토큰을 확인해 주세요.'})
            if urlsplit(self.path).path != '/api/analyze':
                return self.send(404, {'error': '찾을 수 없습니다.'})
            if self.headers.get_content_type() != 'application/json':
                return self.send(415, {'error': 'application/json 요청이 필요합니다.'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 64_000:
                    return self.send(413, {'error': '요청 크기를 초과했습니다.'})
                self.connection.settimeout(10)
                payload = json.loads(self.rfile.read(length))
                session, comments, allow_solar = validate(payload)
                result = engine.analyze(session, comments, allow_solar)
                return self.send(200, result)
            except (ValueError, UnicodeError, TimeoutError):
                return self.send(400, {'error': '요청 형식이 올바르지 않습니다.'})
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception:
                return self.send(500, {'error': '분석에 실패했습니다. 댓글은 변경하지 않았습니다.'})
    return Handler


def main():
    parser = argparse.ArgumentParser(description='유튜브 댓글 필터 로컬 서버')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--demo-data', action='store_true', help='예약된 .test URL 두 개를 시연 목록으로 사용')
    parser.add_argument('--blocklist', type=Path, help='UTF-8 URL 목록 또는 domain:도메인 목록')
    parser.add_argument('--solar', action='store_true', help='UPSTAGE_API_KEY가 있을 때 Solar 기능 허용')
    parser.add_argument('--data-dir', type=Path, default=ROOT/'.local')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')
    args.data_dir.mkdir(parents=True, exist_ok=True)
    token_file = args.data_dir/'connection-token.txt'
    token = os.environ.get('YCSF_TOKEN') or (token_file.read_text().strip() if token_file.exists() else secrets.token_urlsafe(32))
    if len(token) < 24 or len(token) > 200 or not token.isascii():
        parser.error('연결 토큰은 24~200자의 ASCII 문자열이어야 합니다.')
    token_file.write_text(token, encoding='ascii')
    blocklist = Blocklist(args.data_dir/('demo-threats.sqlite3' if args.demo_data else 'threats.sqlite3'))
    if args.demo_data:
        blocklist.import_file(ROOT/'data/demo_blocklist.txt')
    if args.blocklist:
        print(f'Imported: {blocklist.import_file(args.blocklist)}')
    engine = Engine(blocklist, SolarAgent(args.solar))
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_factory(engine, token, args.port, args.demo_data))
    server.daemon_threads = True
    print(f'Comment Guard: http://127.0.0.1:{args.port}/demo', flush=True)
    print(f'Connection token: {token}', flush=True)
    print(f'Blocklist: {blocklist.count} | Solar: {engine.solar.enabled} | Demo: {args.demo_data}', flush=True)
    print('Paste the token into the extension popup. Stop: Ctrl+C', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        blocklist.db.close()


if __name__ == '__main__':
    main()
