"""Expose only the two signed ONLYOFFICE endpoints to the remote Docker bridge.

The upstream is a loopback SSH reverse tunnel, never an arbitrary request URL.
"""
import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class Proxy(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass  # File URLs contain bearer tokens.

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        allowed = {('GET', '/api/office/file'), ('POST', '/api/office/callback')}
        if (self.command, urlsplit(self.path).path) not in allowed:
            self.send_error(404)
            return
        connection = None
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 24 * 1024 * 1024:
                self.send_error(413)
                return
            body = self.rfile.read(length) if length else None
            headers = {key: self.headers[key] for key in ('Authorization', 'Content-Type') if key in self.headers}
            connection = http.client.HTTPConnection('127.0.0.1', self.server.upstream_port, timeout=30)
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            content = response.read(25 * 1024 * 1024)
            self.send_response(response.status)
            self.send_header('Content-Type', response.getheader('Content-Type', 'application/octet-stream'))
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(content)
        except (OSError, ValueError, http.client.HTTPException):
            self.send_error(502, 'Local platform SSH tunnel unavailable')
        finally:
            if connection:
                connection.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='172.17.0.1')
    parser.add_argument('--port', type=int, default=18766)
    parser.add_argument('--upstream-port', type=int, default=18765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Proxy)
    server.upstream_port = args.upstream_port
    server.serve_forever()
