"""Real HTTP and stdio checks against an isolated platform database."""
import asyncio
import importlib.util
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import server

HAS_MCP = importlib.util.find_spec('mcp') is not None
if HAS_MCP:
    import mcp_server as adapter
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client


@unittest.skipUnless(HAS_MCP, 'install requirements-mcp.txt to test MCP')
class MCPTest(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(server, 'DB', Path(folder) / 'mcp.db'))
        self.enterContext(patch.dict(os.environ, {'STANDARD_ADMIN_PASSWORD': 'mcp-test-password',
                                                'STANDARD_MCP_WRITE': '0'}))
        server.init()
        token = secrets.token_urlsafe(32)
        with server.connection() as db:
            admin = dict(db.execute('SELECT * FROM users').fetchone())
            server.shared_cache.create(db, token, admin['id'], time.time() + 3600)
            server.route(db, admin, 'POST', '/api/users', {},
                         {'username': 'reader', 'role': 'reader', 'password': 'mcp-reader-password',
                          'phone': '13900000001'})
            reader = db.execute("SELECT id FROM users WHERE username='reader'").fetchone()
            self.reader_token = secrets.token_urlsafe(32)
            server.shared_cache.create(db, self.reader_token, reader['id'], time.time() + 3600)
        http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(http.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(http.shutdown)
        self.enterContext(patch.dict(os.environ, {'STANDARD_MCP_URL': f'http://127.0.0.1:{http.server_port}',
                                                 'STANDARD_MCP_SESSION': token}))

    def test_workflow_permissions_and_version_comparison(self):
        with self.assertRaisesRegex(ValueError, '写入已禁用'):
            adapter.create_document('MCP-1', '标准', 1, 'V1', '创建')
        with patch.dict(os.environ, {'STANDARD_MCP_WRITE': '1'}):
            doc_id = adapter.create_document('MCP-1', '标准', 1, 'V1', '创建')['id']
            self.assertEqual(adapter.search_documents('MCP-1')['total'], 1)
            doc = adapter.get_document(doc_id)
            vid = doc['current_version']
            self.assertIn('diff', adapter.compare_versions(doc_id, vid, vid))
            issue = adapter.create_review_issue(doc_id, '检查口径', '补充测试')
            adapter.reply_review_issue(doc_id, issue['id'], '已补充')
            adapter.transition_document(doc_id, 'public', '公开评审')
            with self.assertRaisesRegex(ValueError, '409'):
                adapter.transition_document(doc_id, 'approved', '仍有事项')
            adapter.set_review_issue_state(doc_id, issue['id'], 'closed', '完成')
            adapter.transition_document(doc_id, 'approved', '批准')
            adapter.transition_document(doc_id, 'published', '发布')
            adapter.start_next_cycle(doc_id, 'V2', '下一轮')
            self.assertEqual(adapter.get_document(doc_id)['release_version'], vid)
            with patch.dict(os.environ, {'STANDARD_MCP_SESSION': self.reader_token}):
                with self.assertRaisesRegex(ValueError, '403'):
                    adapter.create_document('DENIED', '禁止', 1, 'V1', '测试')
            with patch.dict(os.environ, {'STANDARD_MCP_SESSION': 'x' * 32}):
                with self.assertRaisesRegex(ValueError, '401'):
                    adapter.current_user()
        with patch.dict(os.environ, {'STANDARD_MCP_URL': 'http://example.com'}):
            with self.assertRaisesRegex(ValueError, 'HTTPS'):
                adapter.current_user()
        with self.assertRaises(ValueError):
            adapter.doc_path('../users')
        with self.assertRaises(ValueError):
            adapter.search_documents(limit=101)

    def test_stdio_discovery_and_call(self):
        async def check():
            params = StdioServerParameters(command=sys.executable,
                args=[str(Path(adapter.__file__).resolve())], env=dict(os.environ))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    self.assertEqual(len(listing.tools), 12)
                    result = await session.call_tool('current_user', {})
                    self.assertFalse(result.isError)
                    result = await session.call_tool('get_document', {'document_id': '../users'})
                    self.assertTrue(result.isError)
                    result = await session.call_tool('create_document', {
                        'number': 'DISABLED', 'title': '禁止写入', 'directory_id': 1,
                        'label': 'V1', 'note': '测试默认只读'})
                    self.assertTrue(result.isError)
        asyncio.run(check())
