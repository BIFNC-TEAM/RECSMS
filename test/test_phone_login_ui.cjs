const projectRoot=require('node:path').resolve(__dirname,'..');
process.chdir(projectRoot);
// Explicit cloud/CAPTCHA fixtures: this test does not send real SMS.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'sms-ui-')),base='http://127.0.0.1:18770',codes=path.join(tmp,'code.json'),config=path.join(tmp,'config.json');
 fs.writeFileSync(config,JSON.stringify({TENCENTCLOUD_SECRET_ID:'fixture-id',TENCENTCLOUD_SECRET_KEY:'fixture-secret',TENCENT_CAPTCHA_APP_ID:'123456789',TENCENT_CAPTCHA_SECRET_KEY:'fixture-captcha-key',TENCENT_SMS_SDK_APP_ID:'fixture-sdk',TENCENT_SMS_SIGN_NAME:'fixture-sign',TENCENT_SMS_TEMPLATE_ID:'fixture-template'}));
 const script=`import json,os,sys,phone_auth,server
def fixture(core,service,action,payload):
 if service=='captcha':return {'CaptchaCode':1 if payload['Ticket'].startswith('fixture-ticket-') else 0}
 with open(os.environ['FIXTURE_CODE_FILE'],'w') as f:json.dump({'phone':payload['PhoneNumberSet'][0],'code':payload['TemplateParamSet'][0]},f)
 return {'SendStatusSet':[{'Code':'Ok'}]}
phone_auth.cloud_call=fixture
sys.argv=['server.py','--port','18770']
server.main()`;
 const child=spawn(process.env.PYTHON_EXECUTABLE||'python',['-u','-c',script],{cwd:projectRoot,env:{...process.env,STANDARD_DB:path.join(tmp,'test.db'),STANDARD_ADMIN_PASSWORD:'sms-ui-password',STANDARD_TENCENT_AUTH_CONFIG:config,FIXTURE_CODE_FILE:codes},stdio:'ignore',windowsHide:true});let browser;
 try{
  for(let i=0;i<100;i++){try{if((await fetch(base)).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
  browser=await chromium.launch({headless:true,channel:'msedge'});const context=await browser.newContext({viewport:{width:1440,height:1000}});
  await context.route('https://turing.captcha.qcloud.com/TJCaptcha.js',r=>r.fulfill({contentType:'text/javascript',body:"window.TencentCaptcha=function(id,cb){this.show=()=>setTimeout(()=>cb({ret:0,ticket:'fixture-ticket-'+Date.now(),randstr:'fixture'}),0)}"}));
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(base);
  const status=await (await context.request.get(base+'/api/phone/status')).json();assert(status.configured);assert(!JSON.stringify(status).includes('fixture-secret'));
  await page.locator('#login-consent').check();await page.locator('#phone-login [name="phone"]').fill('13900139001');await page.getByRole('button',{name:'获取验证码',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.sms-status').textContent.includes('验证码已发送'));
  let code=JSON.parse(fs.readFileSync(codes)).code;await page.locator('#phone-login [name="code"]').fill(code==='000000'?'999999':'000000');await page.getByRole('button',{name:'登录 / 注册',exact:true}).click();await page.getByText('验证码错误，最多可尝试5次',{exact:true}).waitFor();
  await page.locator('#phone-login [name="code"]').fill(code);await page.getByRole('button',{name:'登录 / 注册',exact:true}).click();await page.getByRole('heading',{name:'标准文档库',exact:true}).waitFor();await page.getByRole('button',{name:'跳过教程',exact:true}).click();await page.locator('.onboarding-dialog').waitFor({state:'hidden'});let me=await (await context.request.get(base+'/api/me')).json();assert.equal(me.phone,'13900139001');assert(me.phone_verified);
  await page.getByRole('button',{name:'退出',exact:true}).click();assert.equal(await page.getByRole('button',{name:/微信/}).count(),0);const retired=await context.request.get(base+'/api/wechat/status');assert.equal(retired.status(),410);
  await context.request.post(base+'/api/login',{data:{username:'admin',password:'sms-ui-password',...await(await context.request.get(base+'/api/policies')).json(),accepted:true},headers:{'X-Requested-With':'StandardsPlatform'}});await context.request.post(base+'/api/onboarding',{data:{state:'skipped'},headers:{'X-Requested-With':'StandardsPlatform'}});await page.goto(base);await page.locator('.profile-button').click();await page.getByRole('button',{name:'短信验证绑定手机号',exact:true}).click();await page.locator('#phone-bind [name="phone"]').fill('13900139002');await page.getByRole('button',{name:'获取验证码',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.sms-status').textContent.includes('验证码已发送'));
  code=JSON.parse(fs.readFileSync(codes)).code;await page.locator('#phone-bind [name="code"]').fill(code);await page.locator('#phone-bind button[type="submit"]').click();await page.getByRole('heading',{name:'标准文档库',exact:true}).waitFor();me=await (await context.request.get(base+'/api/me')).json();assert.equal(me.id,1);assert.equal(me.role,'admin');assert.equal(me.phone,'13900139002');assert(me.phone_verified);
  await page.getByRole('button',{name:'退出',exact:true}).click();await page.screenshot({path:path.join(projectRoot,'data/phone-login-ui.png'),fullPage:true});await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
  console.log('PASS: CAPTCHA-gated SMS login, wrong-code retry, session cookie, old-account phone binding, no WeChat, mobile. Explicit fixtures; no real SMS.');
 }finally{if(browser)await browser.close();const exited=new Promise(r=>child.once('exit',r));child.kill();await exited;if(path.dirname(tmp)===os.tmpdir())fs.rmSync(tmp,{recursive:true,force:true})}
})().catch(e=>{console.error(e);process.exitCode=1});
