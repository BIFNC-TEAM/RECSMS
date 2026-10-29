import http.cookiejar
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
import phone_auth
import server
import content_pages


class PhoneAuthTest(unittest.TestCase):
    def setUp(self):
        tmp=self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(server,'DB',Path(tmp)/'test.db'))
        self.enterContext(patch.dict(os.environ,{'STANDARD_ADMIN_PASSWORD':'sms-test-password'}))
        cfg={k:'fixture' for k in ('TENCENTCLOUD_SECRET_ID','TENCENTCLOUD_SECRET_KEY','TENCENT_CAPTCHA_SECRET_KEY','TENCENT_SMS_SDK_APP_ID','TENCENT_SMS_SIGN_NAME','TENCENT_SMS_TEMPLATE_ID')}
        cfg.update(TENCENT_CAPTCHA_APP_ID='123456789',TENCENT_SMS_REGION='ap-guangzhou',TENCENT_SMS_ENDPOINT='sms.tencentcloudapi.com',TENCENT_SMS_SECRET_ID='',TENCENT_SMS_SECRET_KEY='')
        self.enterContext(patch.object(phone_auth,'config',return_value=cfg))
        server.init()
        self.db=self.enterContext(server.connection())
        self.admin=dict(self.db.execute('SELECT * FROM users').fetchone())
        self.calls=[];self.captcha_ok=True;self.sms_ok=True
        def cloud(core,service,action,payload):
            self.calls.append((service,action,payload))
            if service=='captcha':return {'CaptchaCode':1 if self.captcha_ok else 0}
            return {'SendStatusSet':[{'Code':'Ok' if self.sms_ok else 'Failed'}]}
        self.enterContext(patch.object(phone_auth,'cloud_call',side_effect=cloud))

    def send(self,number='13900139001',ticket=None,**extra):
        return phone_auth.send(server,self.db,{'phone':number,'ticket':ticket or 'ticket-'+str(len(self.calls)),'randstr':'fixture',**extra},'203.0.113.1')

    def code(self):
        return [p for service,_,p in self.calls if service=='sms'][-1]['TemplateParamSet'][0]

    def error(self,status,func,*args,**kwargs):
        with self.assertRaises(server.Error) as error:func(*args,**kwargs)
        self.assertEqual(error.exception.status,status)

    def test_captcha_then_sms_hash_expiry_single_use_and_unique_account(self):
        issued=self.send()
        self.assertEqual([c[0] for c in self.calls],['captcha','sms'])
        self.assertEqual(self.calls[1][2]['TemplateParamSet'][1],'5')
        code=self.code()
        self.assertNotIn(code,json.dumps(issued))
        row=self.db.execute('SELECT * FROM sms_challenges').fetchone()
        self.assertNotEqual(row['code_hash'],code)
        result=phone_auth.verify(server,self.db,{'challenge_id':issued['challenge_id'],'code':code,'phone':'18888888888'})
        self.assertEqual(result['status'],200)
        account=self.db.execute('SELECT * FROM users WHERE phone=?',('13900139001',)).fetchone()
        self.assertTrue(account['phone_verified']);self.assertEqual(account['role'],'reader')
        self.assertIsNone(self.db.execute('SELECT * FROM users WHERE phone=?',('18888888888',)).fetchone())
        self.assertEqual(phone_auth.verify(server,self.db,{'challenge_id':issued['challenge_id'],'code':code})['status'],400)
        self.db.execute('UPDATE sms_challenges SET created=created-61')
        another=self.send()
        self.assertEqual(phone_auth.verify(server,self.db,{'challenge_id':another['challenge_id'],'code':self.code()})['status'],200)
        self.assertEqual(self.db.execute('SELECT count(*) FROM users WHERE phone=?',('13900139001',)).fetchone()[0],1)

    def test_fail_closed_and_resend_limits(self):
        self.captcha_ok=False
        self.error(400,self.send)
        self.assertFalse(any(c[0]=='sms' for c in self.calls))
        self.captcha_ok=True
        self.error(400,self.send,ticket='trerror_fake')
        issued=self.send(ticket='single-ticket')
        old_code=self.code()
        self.error(429,self.send)
        self.db.execute('UPDATE sms_challenges SET created=created-61')
        self.db.commit()
        self.error(409,self.send,ticket='single-ticket')
        replacement=self.send(ticket='fresh-ticket')
        self.assertNotEqual(replacement['challenge_id'],issued['challenge_id'])
        self.assertEqual(phone_auth.verify(server,self.db,{'challenge_id':issued['challenge_id'],'code':old_code})['status'],400)
        self.db.execute('UPDATE sms_challenges SET expires=?',(time.time()-1,))
        self.assertEqual(phone_auth.verify(server,self.db,{'challenge_id':replacement['challenge_id'],'code':self.code()})['status'],400)

    def test_provider_failure_is_not_a_valid_code(self):
        self.sms_ok=False
        with self.assertRaises(server.Error) as error:self.send()
        self.assertEqual(error.exception.status,502)
        self.assertIn('Failed',error.exception.message)
        audit=self.db.execute("SELECT detail FROM audit WHERE action='短信发送失败'").fetchone()
        self.assertIn('code=Failed',audit['detail'])
        self.assertNotIn('13900139001',audit['detail'])
        row=self.db.execute('SELECT * FROM sms_challenges').fetchone()
        self.assertEqual(row['send_state'],'failed')
        self.assertTrue(row['consumed'])
        self.assertEqual(phone_auth.verify(server,self.db,{'challenge_id':row['id'],'code':self.code()})['status'],400)

    def test_provider_diagnostics_do_not_expose_raw_response(self):
        cloud=phone_auth.cloud_call.side_effect
        def failure(core,service,action,payload):
            if service=='captcha':return cloud(core,service,action,payload)
            return {'SendStatusSet':[{'Code':'bad-secret\n13900139001','Message':'secret-message'}],
                    'RequestId':'unsafe\nsecret'}
        with patch.object(phone_auth,'cloud_call',side_effect=failure):
            with self.assertRaises(server.Error) as error:self.send()
        self.assertIn('InvalidResponse',error.exception.message)
        audit=self.db.execute("SELECT detail FROM audit WHERE action='短信发送失败'").fetchone()['detail']
        for private in ('bad-secret','13900139001','secret-message','unsafe'):
            self.assertNotIn(private,error.exception.message+audit)

    def test_binding_is_authenticated_and_preserves_old_account(self):
        self.error(401,self.send,purpose='bind')
        issued=phone_auth.send(server,self.db,{'phone':'13900139001','purpose':'bind','ticket':'bind-ticket','randstr':'fixture'},'203.0.113.1',self.admin['id'])
        self.assertEqual(phone_auth.verify(server,self.db,{'challenge_id':issued['challenge_id'],'code':self.code()})['status'],403)
        result=phone_auth.verify(server,self.db,{'challenge_id':issued['challenge_id'],'code':self.code()},self.admin['id'])
        self.assertEqual(result['status'],200)
        self.assertEqual(self.db.execute('SELECT phone FROM users WHERE id=?',(self.admin['id'],)).fetchone()[0],'13900139001')
        self.assertEqual(self.db.execute('SELECT count(*) FROM users').fetchone()[0],1)

    def test_persistent_five_attempt_limit_and_http_cookie(self):
        self.db.commit()
        httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);httpd.attempts={}
        threading.Thread(target=httpd.serve_forever,daemon=True).start()
        self.addCleanup(httpd.server_close);self.addCleanup(httpd.shutdown)
        client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        base='http://127.0.0.1:'+str(httpd.server_port)
        def request(path,data=None):
            if data is not None and path in ('phone/send-code', 'phone/verify'):
                data={**data,**content_pages.metadata(),'accepted':True}
            req=urllib.request.Request(base+'/api/'+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json','X-Requested-With':'StandardsPlatform'})
            try:
                with client.open(req) as response:return response.status,json.loads(response.read())
            except urllib.error.HTTPError as error:return error.code,json.loads(error.read())
        _,issued=request('phone/send-code',{'phone':'13900139001','ticket':'http-ticket','randstr':'fixture'})
        code=self.code()
        wrong='000000' if code!='000000' else '999999'
        for _ in range(5):self.assertEqual(request('phone/verify',{'challenge_id':issued['challenge_id'],'code':wrong})[0],400)
        self.assertEqual(request('phone/verify',{'challenge_id':issued['challenge_id'],'code':code})[0],400)
        self.db.execute('UPDATE sms_challenges SET created=created-61');self.db.commit()
        _,fresh=request('phone/send-code',{'phone':'13900139001','ticket':'new-http-ticket','randstr':'fixture'})
        self.assertEqual(request('phone/verify',{'challenge_id':fresh['challenge_id'],'code':self.code()})[0],200)
        self.assertEqual(request('me')[1]['phone'],'13900139001')
        self.assertEqual(request('wechat/status')[0],410)

    def test_untrusted_forwarded_ip_is_ignored(self):
        with patch.dict(os.environ,{'STANDARD_TRUSTED_PROXIES':''}):
            self.assertEqual(phone_auth.client_ip('203.0.113.2',{'X-Forwarded-For':'1.2.3.4'}),'203.0.113.2')
        with patch.dict(os.environ,{'STANDARD_TRUSTED_PROXIES':'127.0.0.1/32'}):
            self.assertEqual(phone_auth.client_ip('127.0.0.1',{'X-Forwarded-For':'1.2.3.4'}),'1.2.3.4')


if __name__=='__main__':unittest.main()
