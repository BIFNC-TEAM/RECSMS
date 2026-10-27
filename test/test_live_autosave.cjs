const projectRoot=require('node:path').resolve(__dirname,'..');
process.chdir(projectRoot);
// Real ONLYOFFICE integration: creates one clearly-labelled test document.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),path=require('node:path');
(async()=>{
 const base='http://127.0.0.1:8765',browser=await chromium.launch({headless:true,channel:'msedge'});
 try{
  const ctx=await browser.newContext({viewport:{width:1600,height:1100}});
  async function api(url,data){const r=data?await ctx.request.post(base+'/api/'+url,{data,headers:{'X-Requested-With':'StandardsPlatform'}}):await ctx.request.get(base+'/api/'+url);assert(r.ok(),await r.text());return r.json()}
  await api('login',{username:'demo_writer',password:'DemoTest@2026!'});
  const marker='Live autosave verification '+Date.now();
  const id=(await api('documents',{number:'AUTOSAVE-'+Date.now(),title:'【测试演示】编辑中自动保存验证',directory_id:1,label:'V0.1',note:'验证不关闭编辑器即可保存到平台',body:'编辑期间自动保存测试。'})).id;
  const page=await ctx.newPage();await page.goto(base);await page.locator('a[data-doc="'+id+'"]').first().click();await page.getByRole('button',{name:'编辑文档',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#office-status')?.textContent.includes('自动保存'),null,{timeout:90000});
  const element=await page.locator('.office-dialog iframe').elementHandle(),frame=await element.contentFrame();assert(frame);
  await frame.locator('#area_id').focus();await page.keyboard.press('Control+End');await page.keyboard.press('Enter');await page.keyboard.insertText(marker);
  let saved;
  for(let i=0;i<45;i++){saved=await api('documents/'+id);if(saved.working_body?.includes(marker))break;await page.waitForTimeout(1000)}
  assert(saved.working_body?.includes(marker),'No autosave while editor remains open');
  assert.equal(saved.session.state,'editing');assert(saved.last_saved_at);assert.equal(saved.autosave_seconds,5);assert.equal(saved.versions.length,1);
  const download=await ctx.request.get(base+'/api/download?doc_id='+id+'&working=1');assert(download.ok());assert.equal((await download.body())[0],0x50);
  await ctx.setOffline(true);await page.waitForFunction(()=>document.querySelector('#office-status')?.textContent.includes('网络已断开'));
  await page.screenshot({path:path.join(projectRoot,'data/autosave-offline-status.png'),fullPage:true});
  await ctx.setOffline(false);await page.waitForFunction(()=>document.querySelector('#office-status')?.textContent.includes('上次保存'),null,{timeout:15000});
  await page.screenshot({path:path.join(projectRoot,'data/autosave-live-status.png'),fullPage:true});
  await page.getByRole('button',{name:'关闭编辑',exact:true}).click();await page.locator('.office-dialog').waitFor({state:'detached',timeout:90000});
  const final=await api('documents/'+id);assert(final.versions[0].body.includes(marker));assert.equal(final.session,null);
  console.log('PASS: real editor saved while still open; durable working download, offline warning, reconnect, final snapshot. Test document id='+id);
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
