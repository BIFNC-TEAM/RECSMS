import http.cookiejar
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
import content_pages
import server
import workspace


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        folder=self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(server,'DB',Path(folder)/'test.db'))
        self.enterContext(patch.dict(os.environ,{'STANDARD_ADMIN_PASSWORD':'workspace-test-password'}))
        server.init()
        self.db=self.enterContext(server.connection())
        self.admin=dict(self.db.execute('SELECT * FROM users').fetchone())

    def call(self, user, path, data=None, query=None):
        return server.route(self.db,user,'GET' if data is None else 'POST','/api/'+path,query or {},data or {})

    def test_taxonomy_is_idempotent_and_new_document_defaults_to_blank(self):
        initial=self.call(self.admin,'directories')
        self.assertEqual(len(initial),len(workspace.TAXONOMY))
        workspace.init(server,self.db)
        self.assertEqual(self.call(self.admin,'directories'),initial)
        doc=self.call(self.admin,'documents',dict(number='BLANK',title='Empty document',directory_id=1,label='V0.1',note='initial'))
        self.assertEqual(self.call(self.admin,'documents/'+str(doc['id']))['versions'][0]['body'],'')
        leaf=next(d for d in initial if d['name']=='机器人标准')
        self.call(self.admin,'documents',dict(number='NESTED',title='Nested',directory_id=leaf['id'],label='V0.1',note='initial'))
        subtree=self.call(self.admin,'documents',query={'directory_id':str(leaf['id'])})
        self.assertEqual([d['number'] for d in subtree],['NESTED'])

    def test_favorites_are_personal_idempotent_and_never_bypass_access(self):
        self.call(self.admin,'users',dict(username='reader',phone='13900000011',password='workspace-reader-password',role='reader'))
        reader=dict(self.db.execute("SELECT * FROM users WHERE username='reader'").fetchone())
        self.assertIsNone(self.call(self.admin,'me')['onboarding_state'])
        self.call(reader,'onboarding',{'state':'skipped','user_id':self.admin['id']})
        self.assertIsNone(self.call(self.admin,'me')['onboarding_state'])
        self.assertEqual(self.call(reader,'me')['onboarding_state'],'skipped')
        self.call(reader,'onboarding',{'state':'done'})
        self.call(reader,'onboarding',{'state':'skipped'})
        self.assertEqual(self.call(reader,'me')['onboarding_state'],'done')
        did=self.call(self.admin,'documents',dict(number='FAV',title='Favorite',directory_id=1,label='V0.1',note='initial'))['id']
        path='documents/'+str(did)
        self.call(reader,path+'/favorite',{'favorite':True,'user_id':self.admin['id']})
        self.call(reader,path+'/favorite',{'favorite':True})
        self.assertEqual(len(self.call(reader,'documents',query={'favorite':'1'})),1)
        self.assertEqual(self.call(self.admin,'documents',query={'favorite':'1'}),[])
        self.assertTrue(self.call(reader,path)['favorite'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM favorites').fetchone()[0],1)
        self.call(reader,path+'/favorite',{'favorite':False})
        self.assertEqual(self.call(reader,'documents',query={'favorite':'1'}),[])
        self.call(reader,path+'/favorite',{'favorite':True})
        self.call(self.admin,'grants',dict(scope='document',target_id=did,user_id=self.admin['id'],access='read'))
        self.assertEqual(len(self.call(reader,'documents',query={'favorite':'1'})),1)
        self.assertFalse(self.call(reader,path)['can_edit'])

    def test_http_consent_gate_and_public_drafts(self):
        self.db.commit()
        httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);httpd.attempts={}
        threading.Thread(target=httpd.serve_forever,daemon=True).start()
        self.addCleanup(httpd.server_close);self.addCleanup(httpd.shutdown)
        client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        base='http://127.0.0.1:'+str(httpd.server_port)
        def request(path,data=None):
            req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json','X-Requested-With':'StandardsPlatform'})
            try:
                with client.open(req) as r:return r.status,r.read().decode()
            except urllib.error.HTTPError as r:return r.code,r.read().decode()
        for path in ('/legal/terms','/legal/privacy','/help'):
            status,html=request(path);self.assertEqual(status,200);self.assertIn('<article',html)
        _,body=request('/api/policies');policy=json.loads(body);self.assertTrue(policy['preview'])
        account=dict(username='admin',password='workspace-test-password')
        self.assertEqual(request('/api/login',account)[0],400)
        self.assertEqual(request('/api/login',{**account,**policy,'accepted':1})[0],400)
        self.assertEqual(request('/api/login',{**account,**policy,'accepted':True,'terms_version':'old'})[0],409)
        self.assertEqual(request('/api/phone/send-code',{'phone':'13900000000'})[0],400)
        self.assertEqual(request('/api/phone/verify',{'challenge_id':'fixture','code':'123456'})[0],400)
        self.assertEqual(self.db.execute('SELECT count(*) FROM policy_acceptances').fetchone()[0],0)
        accepted={**account,**policy,'accepted':True}
        self.assertEqual(request('/api/login',accepted)[0],200)
        self.assertEqual(request('/api/login',accepted)[0],200)
        row=self.db.execute('SELECT * FROM policy_acceptances').fetchone()
        self.assertEqual(row['user_id'],self.admin['id']);self.assertEqual(row['preview'],1)
        self.assertEqual(row['method'],'password');self.assertEqual(len(row['terms_hash']),64)
        self.assertEqual(self.db.execute('SELECT count(*) FROM policy_acceptances').fetchone()[0],1)
        self.assertEqual(request('/api/me')[0],200)
        self.assertEqual(request('/api/onboarding',{'state':'invalid'})[0],400)
