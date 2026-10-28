import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
import threading
import http.cookiejar
import json
from http.server import ThreadingHTTPServer
import urllib.request
from unittest.mock import patch
import accounts
import server
import review_issues


class AccountsTest(unittest.TestCase):
    def setUp(self):
        tmp=self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(server,'DB',Path(tmp)/'test.db'))
        self.enterContext(patch.dict(os.environ,{'STANDARD_ADMIN_PASSWORD':'accounts-test-password'}))
        server.init()
        self.db=self.enterContext(server.connection())
        self.admin=dict(self.db.execute('SELECT * FROM users').fetchone())
        for name in ('owner','editor','manager'):
            self.call(self.admin,'users',{'username':name,'password':'accounts-test-password','role':'editor' if name=='owner' else 'reader','phone':{'owner':'13700000001','editor':'13800138001','manager':'13800138002'}[name]})
        self.users={r['username']:dict(r) for r in self.db.execute('SELECT * FROM users')}
        self.owner,self.editor,self.manager=[self.users[n] for n in ('owner','editor','manager')]
        for user,phone in [(self.editor,'13800138001'),(self.manager,'13800138002')]:
            self.db.execute('UPDATE users SET phone=?,phone_verified=1 WHERE id=?',(phone,user['id']))
        self.id=self.call(self.owner,'documents',{'number':'MEMBERS-1','title':'成员测试','directory_id':1,'label':'V0.1','note':'创建','body':'正文'})['id']

    def call(self,user,path,data=None):
        return server.route(self.db,user,'GET' if data is None else 'POST','/api/'+path,{},data or {})

    def doc(self,user=None):
        return self.call(user or self.admin,f'documents/{self.id}')

    def action(self,user,action,**data):
        return self.call(user,f'documents/{self.id}/{action}',data)

    def assert_error(self,status,func,*args,**kwargs):
        with self.assertRaises(server.Error) as error:
            func(*args,**kwargs)
        self.assertEqual(error.exception.status,status)

    def test_invite_edit_manage_revoke_and_scoped_permissions(self):
        self.assertFalse(self.doc(self.editor)['can_edit'])
        self.action(self.owner,'members',phone='+86 13800138001',permission='edit')
        self.assertTrue(self.doc(self.editor)['can_edit'])
        self.assertFalse(self.doc(self.editor)['can_manage'])
        self.assert_error(403,self.action,self.editor,'members',phone='13800138002',permission='manage')
        self.action(self.owner,'members',phone='13800138002',permission='manage')
        self.assertTrue(self.doc(self.manager)['can_manage'])
        self.assertEqual(self.db.execute('SELECT role FROM users WHERE id=?',(self.manager['id'],)).fetchone()[0],'reader')
        self.action(self.manager,'transition',target='public',note='文档管理者切换')
        self.assertFalse(self.doc(self.editor)['can_edit'])
        self.assertTrue(self.doc(self.manager)['can_edit'])
        self.assert_error(403,self.call,self.manager,'users',{'username':'forbidden','phone':'13700000002','password':'accounts-test-password','role':'admin'})
        self.action(self.manager,'transition',target='draft',note='返回编辑')
        self.action(self.owner,'members',phone='13800138001',permission='remove')
        self.assertFalse(self.doc(self.editor)['can_edit'])
        self.assert_error(403,server.document,self.db,self.editor,self.id,True)

    def test_pending_phone_verification_uniqueness_and_comments(self):
        self.db.execute('UPDATE users SET phone_verified=0 WHERE id=?',(self.editor['id'],))
        self.action(self.owner,'members',phone='13800138001',permission='edit')
        self.assertFalse(self.doc(self.editor)['can_edit'])
        self.db.execute('UPDATE users SET phone_verified=1 WHERE id=?',(self.editor['id'],))
        self.assertTrue(self.doc(self.editor)['can_edit'])
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute('UPDATE users SET phone=? WHERE id=?',('13800138001',self.manager['id']))
        self.action(self.editor,'issues',title='反馈标题',body='普通评论，独立于评审意见')
        self.assertEqual(len(self.doc()['issues']),1)
        self.assert_error(403,self.action,self.editor,'discussion-settings',enabled=False)
        self.action(self.owner,'discussion-settings',enabled=False)
        self.assert_error(409,self.action,self.editor,'issues',title='反馈标题',body='关闭后禁止')
        self.assertEqual(len(self.doc()['issues']),1)
        self.action(self.owner,'discussion-settings',enabled=True)
        self.action(self.editor,'issues',title='反馈标题',body='重新开启后可以评论')
        self.assertEqual(len(self.doc()['issues']),2)
        self.assertTrue(all(i['title']=='反馈标题' for i in self.doc()['issues']))

    def test_issue_threads_author_permissions_reopen_and_legacy_migration(self):
        result=self.action(self.editor,'issues',title='需要明确接口指标',body='说明问题和依据')
        issue=result['id']
        self.action(self.manager,'issue-reply',issue_id=issue,body='补充另一条测试依据')
        self.assert_error(403,self.action,self.manager,'issue-state',issue_id=issue,state='closed',body='无权关闭他人事项')
        self.action(self.editor,'issue-state',issue_id=issue,state='closed',body='已确认接受并解决')
        self.action(self.owner,'issue-state',issue_id=issue,state='open',body='发现遗漏，重新评审')
        thread=self.doc()['issues'][0]
        self.assertEqual(thread['state'],'open')
        self.assertEqual([e['kind'] for e in thread['events']],['comment','closed','reopened'])
        self.assert_error(404,self.action,self.owner,'issue-reply',issue_id=999999,body='不存在')
        self.db.execute('INSERT INTO opinions(doc_id,version_id,body,author,created) VALUES(?,?,?,?,?)',(self.id,self.doc()['current_version'],'历史未处理意见',self.owner['username'],server.now()))
        self.db.execute('INSERT INTO document_comments(doc_id,version_id,user_id,body,created) VALUES(?,?,?,?,?)',(self.id,self.doc()['current_version'],self.editor['id'],'历史评论完整保留',server.now()))
        self.db.execute('INSERT INTO evaluations(doc_id,version_id,user_id,score,body,created) VALUES(?,?,?,?,?,?)',(self.id,self.doc()['current_version'],self.manager['id'],4,'历史评价完整保留',server.now()))
        review_issues.init(self.db);review_issues.init(self.db)
        migrated=self.doc()['issues']
        self.assertEqual(len(migrated),4)
        pending=next(i for i in migrated if i['legacy_source']=='opinion')
        self.assertEqual(pending['state'],'open');self.assertTrue(pending['blocking'])
        feedback=[i for i in migrated if i['legacy_source'] in ('comment','evaluation')]
        self.assertTrue(all(i['state']=='closed' and not i['blocking'] for i in feedback))
        self.assertIn('4分',next(i for i in feedback if i['legacy_source']=='evaluation')['title'])



if __name__=='__main__':unittest.main()
