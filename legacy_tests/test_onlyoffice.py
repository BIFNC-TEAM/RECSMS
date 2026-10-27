"""Protocol integration tests using an explicit fake Document Server, not the real editor."""
from contextlib import closing, redirect_stdout
import base64
import hashlib
import http.cookiejar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from urllib.parse import urlencode
from unittest.mock import patch
import zipfile

import onlyoffice
import server
import configure_local_office


class OfficeIntegrationTest(unittest.TestCase):
    def test_local_configuration_is_consistent_and_idempotent(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(server, 'DB', Path(tmp) / 'platform.db'))
        self.enterContext(patch.dict(os.environ, {'STANDARD_ADMIN_PASSWORD': 'local-test-password'}))
        output = io.StringIO()
        with redirect_stdout(output):
            configure_local_office.prepare()
            env_before = (Path(tmp) / 'onlyoffice.env').read_text()
            configure_local_office.prepare()
        self.assertEqual(env_before, (Path(tmp) / 'onlyoffice.env').read_text())
        secret = env_before.strip().split('=', 1)[1]
        with server.connection() as db:
            config = onlyoffice.settings(db)
            self.assertEqual(config['secret'], secret)
            self.assertEqual(config['platform_url'], 'http://host.docker.internal:8765')
            self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0], 1)
        self.assertGreaterEqual(len(secret), 32)
        self.assertNotIn(secret, output.getvalue())
        self.assertEqual(len(list(Path(tmp).glob('pre-local-office-*.db'))), 2)

    def test_signed_office_lifecycle(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(server, 'DB', Path(tmp) / 'office.db'))
        self.enterContext(patch.dict(os.environ, {'STANDARD_ADMIN_PASSWORD': 'office-test-password'}))
        for key in ('ONLYOFFICE_PUBLIC_URL', 'ONLYOFFICE_INTERNAL_URL', 'ONLYOFFICE_PLATFORM_URL', 'ONLYOFFICE_JWT_SECRET'):
            self.enterContext(patch.dict(os.environ, {}, clear=False))
            os.environ.pop(key, None)
        server.init()
        original = onlyoffice.make_docx('原始正文\n待修改')
        edited_buffer = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(onlyoffice.make_docx('编辑后的正文\n审批依据'))) as initial, zipfile.ZipFile(edited_buffer, 'w') as result:
            for item in initial.infolist():
                result.writestr(item, initial.read(item.filename))
            result.writestr('word/comments.xml', '<comments>原文件批注及版式保留校验</comments>')
        edited = edited_buffer.getvalue()

        class FakeDocs(BaseHTTPRequestHandler):
            def do_POST(self):
                value = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
                payload = onlyoffice.verify(server, value['token'], secret)
                if self.path != '/command' or payload.get('operation') != 'command' or payload.get('c') != 'forcesave':
                    self.send_error(403); return
                self.send_response(200); self.end_headers(); self.wfile.write(b'{"error":0}')

            def do_GET(self):
                body = {'/healthcheck': b'true', '/result.docx': edited, '/changes.zip': b'example-history', '/bad.docx': b'bad zip'}.get(self.path)
                if self.path == '/redirect':
                    self.send_response(302); self.send_header('Location', '/result.docx'); self.end_headers(); return
                self.send_response(200 if body else 404)
                self.end_headers()
                self.wfile.write(body or b'not found')

            def log_message(self, *_args):
                pass

        def serve(handler):
            httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler)
            httpd.attempts = {}
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            self.addCleanup(httpd.server_close); self.addCleanup(httpd.shutdown)
            return 'http://127.0.0.1:' + str(httpd.server_port)

        docs_url = serve(FakeDocs)
        base = serve(server.Handler)
        secret = 'test-secret-with-at-least-32-characters-long'
        def client():
            return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

        def request(client, path, data=None, expected=200, raw=False, callback=False, token=None):
            headers = {'Content-Type': 'application/json'}
            if not callback:
                headers['X-Requested-With'] = 'StandardsPlatform'
            if token:
                headers['Authorization'] = 'Bearer ' + token
            req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
            try:
                with client.open(req) as response:
                    self.assertEqual(response.status, expected)
                    content = response.read()
                    return content if raw else json.loads(content)
            except urllib.error.HTTPError as error:
                content = error.read().decode()
                self.assertEqual(error.code, expected, content)

        admin, writer, reader, service = [client() for _ in range(4)]
        request(admin, '/api/login', {'username': 'admin', 'password': 'office-test-password'})
        request(admin, '/api/users', {'username': 'writer', 'password': 'writer-test-password', 'role': 'editor'})
        request(admin, '/api/users', {'username': 'reader', 'password': 'reader-test-password', 'role': 'reader'})
        for c, name in [(writer, 'writer'), (reader, 'reader')]:
            request(c, '/api/login', {'username': name, 'password': name + '-test-password'})
        new = {'number': 'OO-001', 'title': 'Word 标准', 'directory_id': 1, 'body': '原始正文', 'label': 'V0.1', 'note': '起草'}
        doc_id = request(writer, '/api/documents', new)['id']
        accounts = request(admin, '/api/users')
        reader_id = next(u['id'] for u in accounts if u['username'] == 'reader')
        request(admin, '/api/grants', {'scope': 'document', 'target_id': doc_id, 'user_id': reader_id, 'access': 'read'})
        path = '/api/documents/' + str(doc_id)
        request(writer, path + '/office-open', {}, expected=503)
        request(reader, '/api/office-settings', expected=403)
        config = {'public_url': docs_url, 'internal_url': docs_url, 'platform_url': base, 'secret': secret}
        response = request(admin, '/api/office-settings', config)
        self.assertTrue(response['configured']); self.assertNotIn('secret', response)
        self.assertTrue(request(admin, '/api/office-health')['reachable'])
        onlyoffice.request_save(server, config, 'protocol-test-key')
        request(writer, path + '/office-upload', {'name': 'standard.docx', 'data': base64.b64encode(original).decode(), 'label': 'V0.2', 'note': '原文件入库'})
        imported = request(writer, path)
        self.assertTrue(imported['has_docx'])
        old_version = imported['versions'][-1]['id']
        compared = request(reader, path + '/office-compare', {'a': old_version, 'b': imported['current_version']})
        self.assertFalse(compared['writable'])
        self.assertEqual(compared['config']['editorConfig']['mode'], 'view')
        self.assertFalse(compared['config']['document']['permissions']['edit'])
        payload = onlyoffice.verify(server, compared['comparison']['token'], secret)
        self.assertEqual(payload, {k: v for k, v in compared['comparison'].items() if k != 'token'})
        self.assertEqual(payload['c'], 'compare')
        self.assertEqual(request(service, payload['url'].replace(base, ''), raw=True), original)
        self.assertEqual(request(reader, path)['current_version'], imported['current_version'])
        request(reader, path + '/office-compare', {'a': old_version, 'b': old_version}, expected=400)
        request(reader, path + '/office-compare', {'a': old_version, 'b': -1}, expected=404)
        self.assertEqual(request(writer, '/api/download?' + urlencode({'doc_id': doc_id}), raw=True), original)
        request(writer, path + '/collab?version_id=' + str(imported['current_version']), expected=409)
        first = request(writer, path + '/office-open', {})
        self.assertTrue(first['config']['document']['permissions']['edit'])
        self.assertTrue(first['config']['document']['permissions']['review'])
        self.assertNotIn('reviewGroups', first['config']['document']['permissions'])
        self.assertTrue(first['config']['editorConfig']['customization']['review']['trackChanges'])
        second = request(admin, path + '/office-open', {})
        self.assertEqual(first['key'], second['key'])
        signed_config = onlyoffice.verify(server, first['config']['token'], secret)
        self.assertEqual(signed_config['document']['key'], first['key'])
        self.assertEqual(request(service, first['config']['document']['url'].removeprefix(base), raw=True), original)
        invalid_file = first['config']['document']['url'].removeprefix(base)
        request(service, invalid_file + 'x', expected=403)
        view = request(reader, path + '/office-open', {})
        self.assertFalse(view['writable']); self.assertFalse(view['config']['document']['permissions']['edit'])
        request(writer, path + '/transition', {'target': 'review', 'note': '不能越过编辑会话'}, expected=409)

        def callback(payload, expected=200, header=True):
            token = onlyoffice.sign({'payload': payload, 'operation': 'callback'}, secret)
            data = payload if header else {**payload, 'token': onlyoffice.sign(payload, secret)}
            return request(service, '/api/office/callback', data, expected=expected, callback=True, token=token if header else None)

        writer_id = next(u['id'] for u in accounts if u['username'] == 'writer')
        callback({'key': first['key'], 'status': 1, 'users': [str(writer_id)]})
        self.assertEqual(request(writer, path + '/office-status')['office_status'], 'editing')
        callback({'key': first['key'], 'status': 1, 'users': []})
        self.assertEqual(request(writer, path + '/office-status')['office_status'], 'saving')
        callback({'key': first['key'], 'status': 1, 'users': [str(writer_id)]})

        payload = {'key': first['key'], 'status': 6, 'url': docs_url + '/result.docx', 'history': {'serverVersion': 'test-only'}, 'changesurl': docs_url + '/changes.zip'}
        request(service, '/api/office/callback', payload, expected=403, callback=True)
        signed = onlyoffice.sign({'payload': payload}, secret)
        request(service, '/api/office/callback', {**payload, 'url': docs_url + '/bad.docx'}, expected=403, callback=True, token=signed)
        callback({**payload, 'url': 'http://127.0.0.1:1/outside'}, expected=403)
        callback({**payload, 'url': docs_url + '/redirect'}, expected=502)
        callback({**payload, 'url': docs_url + '/bad.docx'}, expected=400)
        callback({**payload, 'key': view['key']}, expected=403)
        self.assertEqual(callback(payload), {'error': 0})
        self.assertEqual(request(writer, '/api/download?' + urlencode({'doc_id': doc_id, 'working': 1}), raw=True), edited)
        self.assertEqual(request(writer, '/api/download?' + urlencode({'doc_id': doc_id, 'version_id': imported['current_version']}), raw=True), original)
        self.assertIn('编辑后的正文', request(writer, path)['working_body'])
        info = request(writer, path + '/office-status')['session']
        self.assertEqual(info['state'], 'editing'); self.assertEqual(info['revision'], 1)
        callback(payload)
        self.assertEqual(request(writer, path + '/office-status')['session']['revision'], 1)
        checkpoint = {'key': first['key'], 'revision': 1, 'label': 'V1.0', 'note': '形成正式候选版'}
        request(writer, path + '/office-checkpoint', checkpoint, expected=409)
        callback({**payload, 'status': 2}, header=False)
        automatic = request(writer, path)
        self.assertNotEqual(automatic['current_version'], imported['current_version'])
        self.assertIsNone(automatic['session'])
        self.assertEqual(automatic['office_status'], 'complete')
        request(writer, path + '/office-checkpoint', {**checkpoint, 'revision': 0}, expected=409)
        request(reader, path + '/office-checkpoint', checkpoint, expected=403)
        request(writer, path + '/office-checkpoint', checkpoint)
        current = request(writer, path)
        self.assertIn('编辑后的正文', current['versions'][0]['body'])
        self.assertEqual(request(writer, '/api/download?' + urlencode({'doc_id': doc_id}), raw=True), edited)
        self.assertEqual(request(writer, '/api/download?' + urlencode({'doc_id': doc_id, 'version_id': imported['current_version']}), raw=True), original)
        callback({**payload, 'status': 2})  # Idempotent final callback after checkpoint.
        self.assertEqual(request(writer, path)['current_version'], automatic['current_version'])
        request(service, first['config']['document']['url'].removeprefix(base), expected=403)
        request(writer, path + '/transition', {'target': 'review', 'note': '提交 Word 标准'})
        request(admin, path + '/transition', {'target': 'approved', 'note': '管理员批准'})
        request(admin, path + '/transition', {'target': 'published', 'note': '发布'})
        readonly = request(writer, path + '/office-open', {})
        self.assertEqual(readonly['config']['editorConfig']['mode'], 'view')
        request(writer, path + '/version', {'label': 'V1.1', 'note': '继续修订'})
        self.assertEqual(request(writer, '/api/download?' + urlencode({'doc_id': doc_id}), raw=True), edited)
        # DOCX source and its additional package parts survive clone and backup.
        current = request(writer, path)
        content = request(writer, '/api/download?' + urlencode({'doc_id': doc_id, 'version_id': current['current_version']}), raw=True)
        self.assertEqual(content, edited)
        backup = request(admin, '/api/backup', raw=True)
        backup_path = Path(tmp) / 'snapshot.db'; backup_path.write_bytes(backup)
        with closing(sqlite3.connect(backup_path)) as db:
            self.assertEqual(db.execute('SELECT data FROM office_files WHERE version_id=?', (current['current_version'],)).fetchone()[0], edited)
            self.assertEqual(db.execute('SELECT changes FROM office_sessions WHERE key=?', (first['key'],)).fetchone()[0], b'example-history')


if __name__ == '__main__':
    unittest.main()
