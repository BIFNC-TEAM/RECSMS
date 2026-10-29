import os
from datetime import datetime,timedelta,timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import lifecycle
import onlyoffice
import server
import json
from urllib.parse import urlparse,parse_qs


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(server, 'DB', Path(folder) / 'test.db'))
        self.enterContext(patch.dict(os.environ, {'STANDARD_ADMIN_PASSWORD': 'cycle-test-password'}))
        server.init()
        self.db = self.enterContext(server.connection())
        self.admin = dict(self.db.execute('SELECT * FROM users').fetchone())
        for name, role in [('alice','editor'),('bob','editor'),('reviewer','reviewer'),('reader','reader')]:
            self.call(self.admin, 'users', {'username':name,'role':role,'password':'test-password-2026','phone':{'alice':'13900000001','bob':'13900000002','reviewer':'13900000003','reader':'13900000004'}[name]})
        self.users = {r['username']:dict(r) for r in self.db.execute('SELECT * FROM users')}
        self.alice, self.bob = self.users['alice'], self.users['bob']
        self.id = self.call(self.alice, 'documents', {'number':'CYCLE-1','title':'三阶段测试','directory_id':1,'label':'V0.1','note':'首次编制','body':'基础正文'})['id']

    def call(self, user, path, data=None):
        return server.route(self.db,user,'GET' if data is None else 'POST','/api/'+path,{},data or {})

    def doc(self):
        return self.call(self.admin, f'documents/{self.id}')

    def action(self,user,action,**data):
        return self.call(user, f'documents/{self.id}/{action}',data)

    def phase(self,target):
        return self.action(self.admin,'transition',target=target,note='测试阶段切换')

    def save(self,user,label):
        return self.action(user,'save',expected_version=self.doc()['current_version'],label=label,note='共同修订',body='更新正文 '+label)

    def denied(self,user,action,status,**data):
        with self.assertRaises(server.Error) as result:
            self.action(user,action,**data)
        self.assertEqual(result.exception.status,status)

    def test_shared_edit_public_review_release_and_next_round(self):
        self.assertTrue(self.call(self.bob,f'documents/{self.id}')['can_edit'])
        self.save(self.bob,'V0.2')
        invitation=self.action(self.bob,'shares')
        self.call(self.users['reader'],'shares/join',{'token':invitation['token']})
        self.denied(self.users['reader'],'save',403,label='bad',note='bad',body='bad',expected_version=self.doc()['current_version'])
        self.denied(self.bob,'transition',403,target='public',note='不允许')
        self.denied(self.admin,'transition',409,target='published',note='禁止跳过公开')
        self.phase('public')
        self.assertFalse(self.call(self.bob,f'documents/{self.id}')['can_edit'])
        self.denied(self.bob,'save',403,label='bad',note='bad',body='bad',expected_version=self.doc()['current_version'])
        self.action(self.users['reviewer'],'issues',title='评审事项',body='请补充测试口径')
        self.denied(self.admin,'transition',409,target='approved',note='未闭环')
        self.phase('draft')
        oid=self.doc()['issues'][0]['id']
        self.action(self.admin,'issue-state',issue_id=oid,state='closed',body='已补充')
        self.phase('public')
        self.phase('approved')
        self.save(self.admin,'V0.3')
        self.assertEqual(self.doc()['state'],'public')
        self.assertIsNone(self.doc()['cycle']['approved_version'])
        self.phase('approved')
        self.phase('published')
        release=self.doc()['release_version']
        before=dict(self.db.execute('SELECT * FROM versions WHERE id=?',(release,)).fetchone())
        self.denied(self.admin,'save',409,label='bad',note='bad',body='bad',expected_version=release)
        self.denied(self.admin,'transition',409,target='draft',note='发布不可回退')
        self.action(self.admin,'next-cycle',label='V1.1-编辑',note='下一轮')
        self.assertEqual(self.doc()['state'],'draft')
        self.assertEqual(self.doc()['release_version'],release)
        self.assertNotEqual(self.doc()['current_version'],release)
        self.assertEqual(dict(self.db.execute('SELECT * FROM versions WHERE id=?',(release,)).fetchone()),before)
        for action in ['fork','merge','archive-branch','delete-version']:
            self.denied(self.admin,action,410)

    def test_timers_and_onlyoffice_review_permissions(self):
        config={'public_url':'http://127.0.0.1:8080','internal_url':'http://127.0.0.1:8080','platform_url':'http://127.0.0.1:8765','secret':'test-secret-at-least-thirty-two-characters'}
        with patch.object(onlyoffice,'ready',return_value=config),patch.object(onlyoffice,'download',return_value=b'true'):
            a=self.action(self.alice,'office-open')
            b=self.action(self.bob,'office-open')
            self.assertEqual(a['key'],b['key'])
            self.assertFalse(a['config']['document']['permissions']['edit'])
            self.assertTrue(a['config']['document']['permissions']['review'])
            self.assertTrue(a['config']['editorConfig']['customization']['review']['trackChanges'])
            self.denied(self.admin,'transition',409,target='public',note='正在编辑不可切换')
            self.db.execute("UPDATE office_sessions SET state='committed'")
        self.action(self.admin,'cycle-settings',edit_until=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),public_until=(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat())
        self.db.execute("UPDATE document_cycles SET deadline='2000-01-01T00:00:00+00:00'")
        self.db.commit()
        server.publish_due()
        self.assertEqual(self.doc()['state'],'public')
        config={'public_url':'http://127.0.0.1:8080','internal_url':'http://127.0.0.1:8080','platform_url':'http://127.0.0.1:8765','secret':'test-secret-at-least-thirty-two-characters'}
        with patch.object(onlyoffice,'ready',return_value=config),patch.object(onlyoffice,'download',return_value=b'true'):
            self.assertFalse(self.action(self.bob,'office-open')['writable'])
            self.assertTrue(self.action(self.admin,'office-open')['writable'])
            self.db.execute("UPDATE office_sessions SET state='committed'")
        self.db.execute("UPDATE document_cycles SET deadline='2000-01-01T00:00:00+00:00'")
        self.db.commit()
        server.publish_due()
        self.assertEqual(self.doc()['state'],'public')
        self.assertTrue(self.doc()['cycle']['last_error'])
        self.phase('approved')
        self.db.commit()
        server.publish_due()
        self.assertEqual(self.doc()['state'],'published')
        self.assertIsNone(self.doc()['cycle']['deadline'])

    def test_legacy_migration_preserves_materials_and_removes_parallel_editing(self):
        other=self.call(self.alice,'documents',{'number':'OLD-COPY','title':'旧资料','directory_id':1,'label':'W1','note':'迁移前','body':'不可丢失的旧内容'})['id']
        self.db.execute('INSERT INTO work_copies(copy_doc_id,parent_doc_id,base_version_id) VALUES(?,?,?)',(other,self.id,self.doc()['current_version']))
        self.db.execute("UPDATE documents SET state='approved' WHERE id=?",(self.id,))
        self.db.execute('DELETE FROM lifecycle_migrations')
        self.db.commit()
        server.init()
        self.assertEqual(self.doc()['state'],'public')
        self.assertEqual(self.doc()['legacy_materials'][0]['id'],other)
        self.assertFalse(self.call(self.admin,f'documents/{other}')['can_edit'])
        visible=self.call(self.admin,'documents')
        self.assertEqual([d['id'] for d in visible],[self.id])
        self.assertEqual(self.call(self.admin,f'documents/{other}')['versions'][0]['body'],'不可丢失的旧内容')
        server.init()
        self.assertEqual(self.db.execute('SELECT count(*) FROM legacy_materials').fetchone()[0],1)

    def test_signed_office_save_preserves_public_phase_and_freezes_publication(self):
        config={'public_url':'http://127.0.0.1:8080','internal_url':'http://127.0.0.1:8080','platform_url':'http://127.0.0.1:8765','secret':'test-secret-at-least-thirty-two-characters'}
        self.phase('public')
        with patch.object(onlyoffice,'ready',return_value=config),patch.object(onlyoffice,'download',return_value=b'true'):
            opened=self.action(self.admin,'office-open')
        self.db.commit()
        payload={'key':opened['key'],'status':2,'url':'http://127.0.0.1:8080/result.docx'}
        token=onlyoffice.sign({'payload':payload,'operation':'callback'},config['secret'])
        content=onlyoffice.make_docx('公开阶段管理员修订后的正文')
        with patch.object(onlyoffice,'ready',return_value=config),patch.object(onlyoffice,'download',return_value=content):
            onlyoffice.callback(server,self.db,payload,'Bearer '+token)
            session=self.db.execute('SELECT * FROM office_sessions WHERE key=?',(opened['key'],)).fetchone()
            onlyoffice.archive(server,self.db,session)
        self.assertEqual(self.doc()['state'],'public')
        self.assertEqual(self.doc()['versions'][0]['body'],'公开阶段管理员修订后的正文')
        self.phase('approved')
        self.phase('published')
        release=self.doc()['release_version']
        self.assertEqual(self.db.execute('SELECT data FROM office_files WHERE version_id=?',(release,)).fetchone()[0],content)
        self.action(self.admin,'next-cycle',label='V2-working',note='下一轮')
        self.assertEqual(self.db.execute('SELECT data FROM office_files WHERE version_id=?',(self.doc()['current_version'],)).fetchone()[0],content)

    def test_absolute_deadlines_order_timezone_and_next_round_reset(self):
        base=datetime.now(timezone.utc)+timedelta(days=1)
        edit=base.isoformat();public=(base+timedelta(days=1)).isoformat()
        self.action(self.admin,'cycle-settings',edit_until=edit,public_until=public)
        self.assertEqual(self.doc()['cycle']['deadline'],base.isoformat(timespec='seconds'))
        self.denied(self.admin,'cycle-settings',400,edit_until=public,public_until=edit)
        self.denied(self.admin,'cycle-settings',400,edit_until='2099-01-01T10:00:00',public_until=None)
        self.denied(self.admin,'cycle-settings',400,edit_until='2000-01-01T00:00:00+00:00',public_until=None)
        self.phase('public')
        self.assertEqual(self.doc()['cycle']['deadline'],(base+timedelta(days=1)).isoformat(timespec='seconds'))
        self.phase('approved');self.phase('published')
        self.action(self.admin,'next-cycle',label='W-next',note='新一轮')
        self.assertIsNone(self.doc()['cycle']['deadline'])
        self.assertIsNone(self.doc()['cycle']['edit_until'])
        self.assertIsNone(self.doc()['cycle']['public_until'])

    def test_forcesave_persists_while_editor_open_and_recovers_cached_source(self):
        config={'public_url':'http://127.0.0.1:8080','internal_url':'http://127.0.0.1:8080','platform_url':'http://127.0.0.1:8765','secret':'test-secret-at-least-thirty-two-characters'}
        with patch.object(onlyoffice,'ready',return_value=config),patch.object(onlyoffice,'download',return_value=b'true'):
            opened=self.action(self.admin,'office-open')
        original=self.doc()['current_version'];count=len(self.doc()['versions'])
        self.db.execute('INSERT INTO office_settings VALUES(1,?)',(json.dumps(config),))
        self.db.commit()
        content=onlyoffice.make_docx('实时回写内容——窗口尚未关闭')
        payload={'key':opened['key'],'status':6,'url':'http://127.0.0.1:8080/live.docx'}
        token=onlyoffice.sign({'payload':payload,'operation':'callback'},config['secret'])
        with patch.object(onlyoffice,'ready',return_value=config),patch.object(onlyoffice,'download',return_value=content):
            onlyoffice.callback(server,self.db,payload,'Bearer '+token)
        self.db.commit()
        doc=self.doc()
        self.assertEqual(doc['session']['state'],'editing')
        self.assertEqual(doc['current_version'],original)
        self.assertEqual(len(doc['versions']),count)
        self.assertIn('窗口尚未关闭',doc['working_body'])
        self.assertTrue(doc['last_saved_at']);self.assertEqual(doc['autosave_seconds'],5)
        with patch.object(onlyoffice,'ready',return_value=config),patch.object(onlyoffice,'download',return_value=b'true'):
            preview=self.action(self.admin,'office-open',version_id=original,view=True,working=True)
        preview_query={k:v[0] for k,v in parse_qs(urlparse(preview['config']['document']['url']).query).items()}
        with patch.object(onlyoffice,'ready',return_value=config):
            self.assertEqual(onlyoffice.file(server,self.db,preview_query),content)
        self.assertNotEqual(preview['key'],opened['key'])
        self.assertEqual(preview['config']['editorConfig']['customization']['review']['reviewDisplay'],'final')
        self.assertEqual(preview['config']['editorConfig']['customization']['reviewDisplay'],'final')
        self.assertFalse(preview['writable'])
        self.db.commit()

        query={k:v[0] for k,v in parse_qs(urlparse(opened['config']['document']['url']).query).items()}
        with patch.object(onlyoffice,'ready',return_value=config):
            self.assertEqual(onlyoffice.file(server,self.db,query),content)
        matches=server.route(self.db,self.admin,'GET','/api/documents',{'q':'窗口尚未关闭'},{})
        self.assertEqual([d['id'] for d in matches],[self.id])
        with patch.object(onlyoffice,'request_save',side_effect=OSError('fixture offline')):
            onlyoffice.autosave_once(server)
        self.assertTrue(self.doc()['session']['last_error'])
        self.assertEqual(self.db.execute('SELECT saved FROM office_sessions WHERE key=?',(opened['key'],)).fetchone()[0],content)
        with patch.object(onlyoffice,'request_save',return_value=4) as request:
            onlyoffice.autosave_once(server)
            request.assert_called_once()
        self.assertEqual(self.doc()['session']['last_error'],'')


if __name__=='__main__':
    unittest.main()
