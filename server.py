"""build-mon dashboard server: serves index.html and runs collect.py on the host over SSH."""
import json, os, re, shlex, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).parent
HOST = os.environ.get('BM_HOST', 'agents@192.168.0.117')
COLLECT = (HERE / 'collect.py').read_bytes()
crlf_free = lambda p: Path(p).read_bytes().replace(b'\r', b'')  # files mounted from Windows have CRLF endings
Path('/tmp/known_hosts').write_bytes(crlf_free('/keys/known_hosts'))
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
       '-o', 'HostKeyAlias=agents-home', '-o', 'UserKnownHostsFile=/tmp/known_hosts',
       '-o', 'StrictHostKeyChecking=yes', '-o', 'UpdateHostKeys=no',
       '-o', 'ControlMaster=auto', '-o', 'ControlPath=/tmp/cm-%C', '-o', 'ControlPersist=10m', HOST]
unlocked = False  # ponytail: the key lives in this container's ssh-agent until restart


def remote(*args, timeout=60):
    if not unlocked:
        raise PermissionError('locked')
    r = subprocess.run(SSH + ['python3', '-', *map(shlex.quote, args)],
                       input=COLLECT, capture_output=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(r.stderr.decode().strip() or f'ssh exited with {r.returncode}')
    return json.loads(r.stdout)


def unlock(passphrase):
    global unlocked
    env = dict(os.environ, SSH_ASKPASS='/usr/local/bin/askpass', SSH_ASKPASS_REQUIRE='force', BM_PASS=passphrase)
    r = subprocess.run(['ssh-add', '-'], input=crlf_free('/keys/id'), env=env, capture_output=True, timeout=15)
    if r.returncode:
        raise RuntimeError('Could not unlock the key. Check the passphrase. ' + r.stderr.decode().strip())
    unlocked = True
    try:
        return remote('ping')  # surface connection problems now rather than on the first poll
    except Exception:
        unlocked = False  # stay locked, so polls don't keep hitting sshd with a refused login
        subprocess.run(['ssh-add', '-D'], capture_output=True)
        raise


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body, ctype='application/json'):
        body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def api(self, fn):
        # no login, so refuse DNS-rebinding hosts; the port is only published on 127.0.0.1
        if self.headers.get('Host', '').rsplit(':', 1)[0] not in ('localhost', '127.0.0.1'):
            return self.reply(403, {'error': 'Use http://localhost'})
        try:
            self.reply(200, fn())
        except PermissionError:
            self.reply(401, {'error': 'locked', 'host': HOST})
        except Exception as e:
            self.reply(502, {'error': str(e)})

    def do_GET(self):
        url = urlparse(self.path)
        session = parse_qs(url.query).get('session', ['build-mon'])[0]
        routes = {
            '/api/overview': lambda: remote('overview'),
            '/api/updates': lambda: remote('updates', timeout=120),
            '/api/screen': lambda: remote('screen', session if re.fullmatch(r'[\w-]{1,64}', session) else 'build-mon'),
        }
        if url.path == '/':
            return self.reply(200, (HERE / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        if url.path in routes:
            return self.api(routes[url.path])
        self.reply(404, {'error': 'not found'})

    def do_POST(self):
        # custom header forces a CORS preflight, which this server never grants: blocks cross-site POSTs
        if self.headers.get('X-Build-Mon') != '1':
            return self.reply(403, {'error': 'missing X-Build-Mon header'})
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
        job = re.fullmatch(r'/api/run/(\w+)', self.path)
        if self.path == '/api/unlock':
            return self.api(lambda: unlock(str(body.get('passphrase', ''))))
        if job:
            return self.api(lambda: remote('run', job.group(1)))
        self.reply(404, {'error': 'not found'})

    def log_request(self, code='-', size='-'):
        if code != 200:  # skip the 5-second poll noise
            super().log_request(code, size)


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
