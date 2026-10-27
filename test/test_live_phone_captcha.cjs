const projectRoot=require('node:path').resolve(__dirname,'..');
process.chdir(projectRoot);
// Loads the real Tencent CAPTCHA without solving it or requesting any SMS.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});let sends=0;
  page.on('request',r=>{if(r.url().endsWith('/phone/send-code'))sends++});
  await page.goto('http://127.0.0.1:8765');await page.waitForFunction(()=>typeof showTencentCaptcha==='function');
  const config=await(await page.request.get('http://127.0.0.1:8765/api/phone/status')).json();assert(config.configured);
  await page.evaluate(id=>{window.captchaProbeState='pending';window.captchaDisplayProbe=showTencentCaptcha(id).then(()=>window.captchaProbeState='verified',e=>window.captchaProbeState='error: '+e.message)},config.captcha_app_id);
  await page.waitForFunction(()=>{const f=document.querySelector('iframe.captcha-frame');return window.captchaProbeState!=='pending'||(f?.contentDocument?.querySelector('#loading')?.hidden&&f.contentDocument.querySelectorAll('[class*=captcha]').length>10)},null,{timeout:45000});
  const state=await page.evaluate(()=>window.captchaProbeState);assert(!state.startsWith('error:'),state);
  await page.screenshot({path:path.join(projectRoot,'data/tencent-captcha-live.png'),fullPage:true});
  assert.equal(sends,0);console.log('PASS: real Tencent CAPTCHA displayed under production CSP; no SMS requested or sent.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
