"""Run: python -m unittest -v test_platform.py (isolated temp database)."""
import base64
from contextlib import closing
from datetime import datetime, timedelta, timezone
import http.cookiejar
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from urllib.parse import quote

import server


class PlatformTest(unittest.TestCase):
    def test_management_lifecycle(self):
        with self.subTest('isolated database'):
            tmp = self.enterContext(tempfile.TemporaryDirectory())
            server.DB = Path(tmp) / 'test.db'
            os.environ['STANDARD_ADMIN_PASSWORD'] = 'test-admin-123456'
            server.init()
            httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            httpd.attempts = {}
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(httpd.server_close)
            self.addCleanup(httpd.shutdown)
            base = 'http://127.0.0.1:' + str(httpd.server_port)

            def client():
                return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

            def request(opener, path, data=None, expected=200, binary=False):
                req = urllib.request.Request(base + '/api/' + quote(path, safe='/?=&'), data=json.dumps(data).encode() if data is not None else None,
                                             headers={'Content-Type': 'application/json', 'X-Requested-With': 'StandardsPlatform'})
                try:
                    with opener.open(req) as response:
                        self.assertEqual(response.status, expected)
                        result = response.read()
                        return result if binary else json.loads(result)
                except urllib.error.HTTPError as exc:
                    self.assertEqual(exc.code, expected, exc.read().decode())

            admin, editor, reviewer, reader, anonymous = [client() for _ in range(5)]
            request(anonymous, 'documents', expected=401)
            request(admin, 'login', {'username': 'admin', 'password': 'test-admin-123456'})
            for name, role in [('writer', 'editor'), ('expert', 'reviewer'), ('viewer', 'reader')]:
                request(admin, 'users', {'username': name, 'password': 'long-password-123', 'role': role})
            for opener, name in [(editor, 'writer'), (reviewer, 'expert'), (reader, 'viewer')]:
                request(opener, 'login', {'username': name, 'password': 'long-password-123'})
            users = request(admin, 'users')
            uid = {u['username']: u['id'] for u in users}
            request(admin, 'directories', {'name': '机器人标准', 'parent_id': 1})
            request(admin, 'directories', {'id': 1, 'name': '错误循环', 'parent_id': 2}, expected=400)
            doc_id = request(editor, 'documents', {'number': 'RB-001', 'title': '机器人接口标准', 'directory_id': 2, 'label': 'V0.1', 'note': '起草', 'body': '通信协议\n第一版', 'tags': '机器人,接口'})['id']
            for account in ('viewer', 'expert'):
                request(admin, 'grants', {'scope': 'document', 'target_id': doc_id, 'user_id': uid[account], 'access': 'read'})
            doc = lambda: request(editor, f'documents/{doc_id}')
            post = lambda opener, action, data, expected=200: request(opener, f'documents/{doc_id}/{action}', data, expected)
            old = doc()['current_version']
            post(reader, 'save', {'label': 'illegal', 'note': 'bad', 'body': ''}, 403)
            post(editor, 'save', {'label': 'V0.2', 'note': '补充定义', 'body': '通信协议\n第二版', 'expected_version': old})
            post(editor, 'save', {'label': 'stale', 'note': '过期并发保存', 'body': '', 'expected_version': old}, 409)
            attachment = base64.b64encode(b'attachment-original').decode()
            post(editor, 'attachment', {'label': 'V0.3', 'note': '加入测试方法', 'name': 'test.txt', 'data': attachment})
            first_file = doc()['attachments'][-1]
            post(editor, 'attachment', {'label': 'V0.4', 'note': '更新测试方法', 'name': 'test.txt', 'data': base64.b64encode(b'new').decode()})
            collab_version = doc()['current_version']
            room = request(editor, f'documents/{doc_id}/collab?version_id={collab_version}')
            self.assertTrue(room['writable'])
            update = server.merge_collab({'body': '并发新增正文。'})['state']
            update_request = {'version_id': collab_version, 'client_id': 'unit-client', 'update': update}
            post(reader, 'collab', update_request, 403)
            post(reviewer, 'collab', update_request, 403)
            post(editor, 'collab', {**update_request, 'update': 'invalid'}, 400)
            merged = post(editor, 'collab', update_request)
            # The same Yjs update is idempotent even if a client retries after a lost response.
            room = request(editor, f'documents/{doc_id}/collab?version_id={collab_version}')
            revision = room['revision']
            post(editor, 'collab', update_request)
            self.assertEqual(request(editor, f'documents/{doc_id}/collab?version_id={collab_version}')['revision'], revision)
            post(editor, 'save', {'label': 'overwrite', 'note': '旧编辑器不能丢弃协作修改', 'body': 'bad', 'expected_version': collab_version}, 409)
            checkpoint = {'label': 'V0.4-collab', 'note': '保存协作版本', 'expected_version': collab_version, 'revision': revision, 'body': '客户端伪造正文'}
            post(editor, 'collab-checkpoint', {**checkpoint, 'revision': -1}, 409)
            post(editor, 'collab-checkpoint', checkpoint)
            self.assertIn('并发新增正文。', doc()['versions'][0]['body'])
            self.assertNotIn('客户端伪造正文', doc()['versions'][0]['body'])
            post(editor, 'collab', update_request, 409)
            raw = request(editor, f'download?doc_id={doc_id}&version_id={first_file["version_id"]}&attachment_id={first_file["id"]}', binary=True)
            self.assertEqual(raw, b'attachment-original')
            self.assertEqual(len(request(editor, 'documents?q=第二版&tag=机器人')), 1)
            post(editor, 'transition', {'target': 'review', 'note': '提交管理员审核'})
            post(editor, 'save', {'label': 'V0.5', 'note': '冻结应拒绝', 'body': '', 'expected_version': doc()['current_version']}, 409)
            post(reviewer, 'opinion', {'body': '补充验证依据'})
            post(reviewer, 'transition', {'target': 'approved', 'note': '评审不能替代管理员'}, 403)
            post(admin, 'transition', {'target': 'approved', 'note': '尚有未闭环意见'}, 409)
            opinion = doc()['opinions'][0]['id']
            post(editor, 'resolve', {'opinion_id': opinion, 'status': 'resolved', 'resolution': '附件已提供依据'})
            post(admin, 'transition', {'target': 'approved', 'note': '管理员批准'})
            post(editor, 'schedule', {'publish_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}, 403)
            post(admin, 'schedule', {'publish_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
            self.assertEqual(doc()['state'], 'scheduled')
            server.publish_due()
            self.assertEqual(doc()['state'], 'scheduled')
            post(editor, 'version', {'label': 'forbidden', 'note': '计划冻结'}, 409)
            post(admin, 'cancel-schedule', {})
            self.assertEqual(doc()['state'], 'approved')
            post(admin, 'schedule', {'publish_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
            # Simulate restart with a persisted overdue task, then invoke the worker twice.
            with server.connection() as db:
                db.execute("UPDATE schedules SET publish_at='2000-01-01T00:00:00+00:00' WHERE status='pending'")
            server.publish_due()
            server.publish_due()
            published = doc()
            self.assertEqual(published['state'], 'published')
            self.assertEqual(sum(t['action'] == 'published' for t in published['transitions']), 1)
            release = published['release_version']
            post(reader, 'evaluate', {'version_id': release, 'score': 5, 'body': '清晰可执行'})
            self.assertEqual(doc()['evaluations'][0]['score'], 5)
            post(reader, 'evaluate', {'version_id': old, 'score': 5, 'body': '草稿不能评价'}, 400)
            post(editor, 'version', {'label': 'V1.1-draft', 'note': '新修订', 'body': '尚未审核的新内容'})
            self.assertEqual(doc()['release_version'], release)
            self.assertIn('第二版', request(reader, f'download?doc_id={doc_id}', binary=True).decode())
            post(editor, 'restore', {'label': 'V1.2-draft', 'note': '历史回溯', 'source_id': old})
            self.assertEqual(doc()['versions'][0]['body'], '通信协议\n第一版')
            diff = request(editor, f'documents/{doc_id}/diff?a={old}&b={release}')
            self.assertIn('第二版', diff['diff'])
            # Inherited directory ACL must also protect search, download, and creation.
            request(admin, 'grants', {'scope': 'directory', 'target_id': 1, 'user_id': uid['writer'], 'access': 'edit'})
            request(reader, f'documents/{doc_id}', expected=403)
            request(reader, f'download?doc_id={doc_id}', expected=403)
            self.assertEqual(request(reader, 'documents'), [])
            request(reader, 'audit', expected=403)
            backup = request(admin, 'backup', binary=True)
            backup_path = Path(tmp) / 'backup.db'
            backup_path.write_bytes(backup)
            with closing(sqlite3.connect(backup_path)) as db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone()[0], 0)
                self.assertGreater(db.execute('SELECT count(*) FROM attachments').fetchone()[0], 0)
            restored = Path(tmp) / 'restored.db'
            env = {**os.environ, 'STANDARD_DB': str(restored)}
            result = subprocess.run([sys.executable, str(server.ROOT / 'server.py'), '--restore', str(backup_path)], env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with closing(sqlite3.connect(restored)) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM documents').fetchone()[0], 1)
            self.assertGreater(len(request(admin, 'audit')), 15)
            request(editor, 'logout', {})
            request(editor, 'documents', expected=401)


if __name__ == '__main__':
    unittest.main()
