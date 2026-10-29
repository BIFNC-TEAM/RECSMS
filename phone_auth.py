"""Tencent CAPTCHA-gated SMS login; no development-code or fail-open path."""
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import time
import shared_cache

CONFIG_PATH=Path(__file__).parent/'data/tencent-auth.json'
TTL=300
COOLDOWN=60


def init(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS phone_auth_key(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sms_challenges(id TEXT PRIMARY KEY,phone TEXT NOT NULL,purpose TEXT NOT NULL,bind_user_id INTEGER REFERENCES users(id),code_hash TEXT NOT NULL,expires REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL,ip TEXT NOT NULL,send_state TEXT NOT NULL,consumed INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS sms_phone_time ON sms_challenges(phone,created);
    CREATE INDEX IF NOT EXISTS sms_ip_time ON sms_challenges(ip,created);
    CREATE TABLE IF NOT EXISTS captcha_receipts(ticket_hash TEXT PRIMARY KEY,created REAL NOT NULL);
    ''')
    db.execute('INSERT OR IGNORE INTO phone_auth_key VALUES(1,?)',(secrets.token_urlsafe(48),))


def config():
    path=Path(os.environ.get('STANDARD_TENCENT_AUTH_CONFIG',str(CONFIG_PATH)))
    try:
        values=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    except (ValueError,OSError):
        values={}
    names=['TENCENTCLOUD_SECRET_ID','TENCENTCLOUD_SECRET_KEY','TENCENT_CAPTCHA_APP_ID','TENCENT_CAPTCHA_SECRET_KEY','TENCENT_SMS_SECRET_ID','TENCENT_SMS_SECRET_KEY','TENCENT_SMS_REGION','TENCENT_SMS_ENDPOINT','TENCENT_SMS_SDK_APP_ID','TENCENT_SMS_SIGN_NAME','TENCENT_SMS_TEMPLATE_ID']
    return {name:str(os.environ.get(name,values.get(name,''))).strip() for name in names}


def status():
    cfg=config()
    ready=all(cfg.get(k) for k in ('TENCENTCLOUD_SECRET_ID','TENCENTCLOUD_SECRET_KEY','TENCENT_CAPTCHA_APP_ID','TENCENT_CAPTCHA_SECRET_KEY','TENCENT_SMS_SDK_APP_ID','TENCENT_SMS_SIGN_NAME','TENCENT_SMS_TEMPLATE_ID'))
    return {'configured':ready,'captcha_app_id':cfg['TENCENT_CAPTCHA_APP_ID'] if ready else '', 'cooldown':COOLDOWN,'expires_in':TTL}


def cloud_call(core,service,action,payload):
    cfg=config()
    try:
        from tencentcloud.common.credential import Credential
        from tencentcloud.common.profile.http_profile import HttpProfile
        from tencentcloud.common.profile.client_profile import ClientProfile
        if service=='sms':
            from tencentcloud.sms.v20210111 import sms_client,models
            client_class=sms_client.SmsClient
            model=models.SendSmsRequest
            endpoint=cfg['TENCENT_SMS_ENDPOINT'] or 'sms.tencentcloudapi.com'
            secret_id=cfg['TENCENT_SMS_SECRET_ID'] or cfg['TENCENTCLOUD_SECRET_ID']
            secret_key=cfg['TENCENT_SMS_SECRET_KEY'] or cfg['TENCENTCLOUD_SECRET_KEY']
        else:
            from tencentcloud.captcha.v20190722 import captcha_client,models
            client_class=captcha_client.CaptchaClient
            model=models.DescribeCaptchaResultRequest
            endpoint='captcha.tencentcloudapi.com'
            secret_id,secret_key=cfg['TENCENTCLOUD_SECRET_ID'],cfg['TENCENTCLOUD_SECRET_KEY']
        if not re.fullmatch(r'[a-z0-9.-]+\.tencentcloudapi\.com',endpoint):
            raise core.Error('腾讯云服务域名配置无效',503)
        http=HttpProfile(endpoint=endpoint,reqTimeout=15)
        profile=ClientProfile(httpProfile=http)
        client=client_class(Credential(secret_id,secret_key),cfg['TENCENT_SMS_REGION'] or 'ap-guangzhou',profile)
        request=model();request.from_json_string(json.dumps(payload))
        response=getattr(client,action)(request)
        return json.loads(response.to_json_string())
    except core.Error:
        raise
    except ImportError:
        raise core.Error('缺少腾讯云SDK，请安装 requirements-auth.txt',503)
    except Exception as exc:
        code=getattr(exc,'code','')
        safe=code if re.fullmatch(r'[A-Za-z0-9.]{1,100}',str(code)) else 'Unavailable'
        raise core.Error('腾讯云验证服务暂不可用（'+safe+'）',502)


def client_ip(peer,headers):
    try:
        trusted=[ipaddress.ip_network(v.strip()) for v in os.environ.get('STANDARD_TRUSTED_PROXIES','').split(',') if v.strip()]
        current=ipaddress.ip_address(peer)
        chain=[v.strip() for v in headers.get('X-Forwarded-For','').split(',') if v.strip()]
        for value in reversed(chain):
            if not any(current in net for net in trusted):
                break
            current=ipaddress.ip_address(value)
        return str(current)
    except ValueError:
        return peer


def digest(db,challenge,number,purpose,code):
    key=db.execute('SELECT value FROM phone_auth_key WHERE id=1').fetchone()['value']
    return hmac.new(key.encode(),(challenge+'|'+number+'|'+purpose+'|'+code).encode(),hashlib.sha256).hexdigest()


def send(core,db,data,ip,user_id=None):
    if not status()['configured']:
        raise core.Error('尚未配置腾讯云短信与图形验证服务',503)
    number=core.accounts.phone(core,data.get('phone'))
    purpose=data.get('purpose','login')
    if purpose not in ('login','bind'):
        raise core.Error('验证码用途无效')
    bound=None
    if purpose=='bind':
        if not user_id:
            raise core.Error('请先登录后绑定手机号',401)
        bound=user_id
        existing=db.execute('SELECT id FROM users WHERE phone=?',(number,)).fetchone()
        if existing and existing['id']!=bound:
            raise core.Error('该手机号已属于其他账号，不能重复绑定',409)
        current=db.execute('SELECT phone FROM users WHERE id=?',(bound,)).fetchone()
        if current['phone'] and current['phone']!=number:
            raise core.Error('已绑定手机号不可在此更换，请联系管理员',409)
    ticket=core.required(data,'ticket',4096)
    randstr=core.required(data,'randstr',1024)
    if ticket.startswith('trerror'):
        raise core.Error('图形验证未完成，请重新验证',400)
    cfg=config()
    result=cloud_call(core,'captcha','DescribeCaptchaResult',{'CaptchaType':9,'Ticket':ticket,'Randstr':randstr,'UserIp':ip,'CaptchaAppId':int(cfg['TENCENT_CAPTCHA_APP_ID']),'AppSecretKey':cfg['TENCENT_CAPTCHA_SECRET_KEY']})
    if result.get('CaptchaCode')!=1:
        raise core.Error('图形验证失败，请重新验证',400)
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    now=time.time()
    recent=db.execute('SELECT MAX(created) AS recent FROM sms_challenges WHERE phone=?',(number,)).fetchone()['recent']
    if recent and now-recent<COOLDOWN:
        raise core.Error('发送过于频繁，请60秒后重试',429)
    for column,value,span,limit in [('phone',number,3600,5),('phone',number,86400,10),('ip',ip,3600,20),('ip',ip,86400,60)]:
        if db.execute('SELECT count(*) FROM sms_challenges WHERE '+column+'=? AND created>?',(value,now-span)).fetchone()[0]>=limit:
            raise core.Error('验证码发送次数已达上限，请稍后再试',429)
    ticket_hash=hashlib.sha256(ticket.encode()).hexdigest()
    if db.execute('SELECT 1 FROM captcha_receipts WHERE ticket_hash=?',(ticket_hash,)).fetchone():
        raise core.Error('图形验证已使用，请重新验证',409)
    db.execute('DELETE FROM captcha_receipts WHERE created<?',(now-86400,))
    db.execute('INSERT INTO captcha_receipts VALUES(?,?)',(ticket_hash,now))
    challenge=secrets.token_urlsafe(24)
    code=str(secrets.randbelow(1000000)).zfill(6)
    db.execute('INSERT INTO sms_challenges(id,phone,purpose,bind_user_id,code_hash,expires,created,ip,send_state) VALUES(?,?,?,?,?,?,?,?,\'pending\')',(challenge,number,purpose,bound,digest(db,challenge,number,purpose,code),now+TTL,now,ip))
    db.commit()  # Reserve limits before the paid request, without holding a database lock on the network.
    try:
        response=cloud_call(core,'sms','SendSms',{'PhoneNumberSet':['+86'+number],'SmsSdkAppId':cfg['TENCENT_SMS_SDK_APP_ID'],'SignName':cfg['TENCENT_SMS_SIGN_NAME'],'TemplateId':cfg['TENCENT_SMS_TEMPLATE_ID'],'TemplateParamSet':[code,str(TTL//60)]})
        statuses=response.get('SendStatusSet') or []
        if len(statuses)!=1 or statuses[0].get('Code')!='Ok':
            provider_code=statuses[0].get('Code') if len(statuses)==1 else None
            safe_code=provider_code if isinstance(provider_code,str) and re.fullmatch(r'[A-Za-z0-9.]{1,100}',provider_code) else 'InvalidResponse'
            request_id=response.get('RequestId','')
            safe_id=request_id if isinstance(request_id,str) and re.fullmatch(r'[A-Za-z0-9-]{1,100}',request_id) else ''
            core.audit(db,{'username':'sms-auth'},'短信发送失败',detail=number[:3]+'****'+number[-4:]+' code='+safe_code+' request_id='+safe_id)
            raise core.Error('短信发送失败（'+safe_code+'），请联系管理员核查',502)
    except core.Error:
        db.execute("UPDATE sms_challenges SET send_state='failed',consumed=1 WHERE id=?",(challenge,))
        db.commit()
        raise
    db.execute("UPDATE sms_challenges SET consumed=1 WHERE phone=? AND purpose=? AND COALESCE(bind_user_id,0)=? AND id<>? AND send_state='sent'",(number,purpose,bound or 0,challenge))
    db.execute("UPDATE sms_challenges SET send_state='sent' WHERE id=?",(challenge,))
    core.audit(db,{'username':'sms-auth'},'发送手机验证码',detail=number[:3]+'****'+number[-4:]+' '+purpose)
    return {'challenge_id':challenge,'cooldown':COOLDOWN,'expires_in':TTL}


def verify(core,db,data,user_id=None):
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    row=db.execute('SELECT * FROM sms_challenges WHERE id=?',(str(data.get('challenge_id','')),)).fetchone()
    failure={'error':'验证码无效或已过期，请重新获取','status':400}
    if not row or row['consumed'] or row['send_state']!='sent' or row['expires']<=time.time() or row['attempts']>=5:
        return failure
    if row['purpose']=='bind' and row['bind_user_id']!=user_id:
        return {'error':'绑定请求与当前账号不匹配','status':403}
    code=str(data.get('code',''))
    expected=digest(db,row['id'],row['phone'],row['purpose'],code)
    if not re.fullmatch(r'\d{6}',code) or not hmac.compare_digest(expected,row['code_hash']):
        db.execute('UPDATE sms_challenges SET attempts=attempts+1,consumed=CASE WHEN attempts+1>=5 THEN 1 ELSE 0 END WHERE id=?',(row['id'],))
        return {'error':'验证码错误，最多可尝试5次','status':400}
    existing=db.execute('SELECT * FROM users WHERE phone=?',(row['phone'],)).fetchone()
    if row['purpose']=='bind':
        uid=row['bind_user_id']
        current=db.execute('SELECT phone FROM users WHERE id=?',(uid,)).fetchone()
        if not current or (existing and existing['id']!=uid) or (current['phone'] and current['phone']!=row['phone']):
            return {'error':'手机号绑定信息发生变化，请重新发起验证','status':409}
        db.execute('UPDATE users SET phone=?,phone_verified=1 WHERE id=?',(row['phone'],uid))
    elif existing:
        uid=existing['id']
        db.execute('UPDATE users SET phone_verified=1 WHERE id=?',(uid,))
    else:
        uid=db.execute('INSERT INTO users(username,password,role,phone,phone_verified) VALUES(?,?,\'reader\',?,1)',('用户'+row['phone'][-4:]+'_'+secrets.token_hex(3),core.password_hash(secrets.token_urlsafe(32)),row['phone'])).lastrowid
    db.execute('UPDATE sms_challenges SET consumed=1 WHERE id=?',(row['id'],))
    core.accounts.notify_invites(core,db,uid,row['phone'])
    account=dict(db.execute('SELECT id,username,role FROM users WHERE id=?',(uid,)).fetchone())
    core.audit(db,account,'短信手机号验证',detail=row['phone'][:3]+'****'+row['phone'][-4:])
    token=secrets.token_urlsafe(32)
    shared_cache.create(db,token,uid,time.time()+8*3600)
    return {'token':token,'status':200,'user_id':uid}


def clear_ephemeral(db):
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for name in ('sms_challenges','captcha_receipts','wx_login_tickets','wx_access_token'):
        if name in tables:
            db.execute('DELETE FROM '+name)
