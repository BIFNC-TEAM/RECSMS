const projectRoot=require('node:path').resolve(__dirname,'..');
process.chdir(projectRoot);
// Optional browser verification: PLAYWRIGHT_MODULE may point to a bundled installation.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const assert=require('node:assert/strict');

(async()=>{
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'standard-browser-'));
  const python=process.env.PYTHON_EXECUTABLE||'python';
  const start=()=>spawn(python,['-u',path.join(projectRoot,'server.py'),'--port','18765'],{env:{...process.env,STANDARD_DB:path.join(temp,'browser.db'),STANDARD_ADMIN_PASSWORD:'browser-test-password'},stdio:'ignore',windowsHide:true});
  let child=start();
  const base='http://127.0.0.1:18765';
  let browser;
  try{
    let ready=false;
    for(let i=0;i<60;i++){try{if((await fetch(base)).ok){ready=true;break}}catch{}await new Promise(r=>setTimeout(r,100))}
    assert(ready,'test server did not start');
    browser=await chromium.launch({headless:true,...(process.env.BROWSER_CHANNEL?{channel:process.env.BROWSER_CHANNEL}:{})});
    const admin=await browser.newContext({viewport:{width:1440,height:1000}});
    const page=await admin.newPage();
    const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base);
    await page.getByLabel('账号',{exact:true}).fill('admin');
    await page.getByLabel('密码',{exact:true}).fill('browser-test-password');
    await page.getByRole('button',{name:'登录工作空间'}).click();
    await page.getByRole('heading',{name:'标准文档库'}).waitFor();
    await page.getByRole('button',{name:'管理与审计',exact:false}).click();
    await page.getByRole('button',{name:'新增用户',exact:true}).click();
    await page.getByLabel('账号',{exact:true}).fill('writer');
    await page.getByLabel('密码（至少 12 位）').fill('writer-test-password');
    await page.getByLabel('角色',{exact:true}).selectOption('editor');
    await page.getByRole('button',{name:'确认',exact:true}).click();
    await page.locator('dialog').waitFor({state:'hidden'});
    const editor=await browser.newContext({viewport:{width:1440,height:1000}});
    const writer=await editor.newPage();
    writer.on('pageerror',e=>errors.push(e.message));
    await writer.goto(base);
    await writer.getByLabel('账号',{exact:true}).fill('writer');
    await writer.getByLabel('密码',{exact:true}).fill('writer-test-password');
    await writer.getByRole('button',{name:'登录工作空间'}).click();
    await writer.getByRole('button',{name:'新建工作副本',exact:false}).click();
    await writer.getByLabel('标准编号',{exact:true}).fill('T/ROBOT 001—2026');
    await writer.getByLabel('文档名称',{exact:true}).fill('机器人与国产操作系统接口兼容性要求');
    await writer.getByLabel('标签（逗号分隔）').fill('机器人,国产操作系统,接口标准');
    await writer.getByLabel('正文',{exact:true}).fill('1 范围\n本文件规定机器人与国产操作系统接口兼容性要求。\n\n2 接口要求\n接口应提供明确的版本信息和错误反馈。\n\n3 验证方法\n测试结果应与具体标准版本关联，并保留验证材料。');
    await writer.getByRole('button',{name:'确认',exact:true}).click();
    await writer.locator('#modal').waitFor({state:'hidden'});
    // A share fragment survives login and grants only this private working copy.
    const added=await admin.request.post(base+'/api/users',{data:{username:'shared_reader',password:'shared-reader-password',role:'reader'},headers:{'X-Requested-With':'StandardsPlatform'}});
    assert(added.ok());
    await writer.getByRole('button',{name:'分享协作',exact:true}).click();
    const shareURL=await writer.getByLabel('复制此链接发送给协作者',{exact:true}).inputValue();
    await writer.getByRole('button',{name:'确认',exact:true}).click();
    const invited=await browser.newContext({viewport:{width:1440,height:1000}}),invitePage=await invited.newPage();
    invitePage.on('pageerror',e=>errors.push(e.message));
    await invitePage.goto(shareURL);
    await invitePage.getByLabel('账号',{exact:true}).fill('shared_reader');await invitePage.getByLabel('密码',{exact:true}).fill('shared-reader-password');
    await invitePage.getByRole('button',{name:'登录工作空间'}).click();
    await invitePage.getByRole('heading',{name:'机器人与国产操作系统接口兼容性要求',exact:true}).waitFor();
    assert(await invitePage.getByRole('button',{name:'提交文档',exact:true}).isDisabled());
    assert(await invitePage.getByRole('button',{name:'分享协作',exact:true}).isDisabled());
    assert.equal(await invitePage.getByRole('button',{name:'实时协作',exact:true}).count(),1);
    await invited.close();
    await page.getByRole('button',{name:'标准文档库',exact:false}).click();
    await page.getByRole('link',{name:'机器人与国产操作系统接口兼容性要求',exact:true}).click();
    await Promise.all([
      writer.getByRole('button',{name:'实时协作',exact:true}).click(),
      page.getByRole('button',{name:'实时协作',exact:true}).click()
    ]);
    const a=writer.locator('#collab-body'),b=page.locator('#collab-body');
    await a.waitFor();await b.waitFor();
    await writer.getByText('在线协作者：admin、writer',{exact:false}).waitFor().catch(async()=>{
      await writer.getByText('在线协作者：writer、admin',{exact:false}).waitFor();
    });
    // Concurrent offline edits from the same initial state must both survive reconnect.
    await Promise.all([editor.setOffline(true),admin.setOffline(true)]);
    const before=await a.inputValue();
    await Promise.all([a.fill('起草人补充。\n'+before),b.fill('管理员补充。\n'+before)]);
    await new Promise(r=>setTimeout(r,900));
    await Promise.all([editor.setOffline(false),admin.setOffline(false)]);
    await writer.waitForFunction(()=>document.querySelector('#collab-body').value.includes('管理员补充。'));
    await page.waitForFunction(()=>document.querySelector('#collab-body').value.includes('起草人补充。'));
    const merged=await a.inputValue();
    assert.equal(merged,await b.inputValue());
    assert.equal(merged.split('起草人补充。').length,2);
    assert.equal(merged.split('管理员补充。').length,2);
    fs.mkdirSync(path.join(projectRoot,'data'),{recursive:true});
    await writer.screenshot({path:path.join(projectRoot,'data/collab-preview.png'),fullPage:true});
    await writer.getByRole('button',{name:'同步并关闭',exact:true}).click();
    await writer.locator('.collab-dialog').waitFor({state:'detached'});
    // Restart the actual service process, keeping the database and signed-in browser sessions.
    const stopped=new Promise(r=>child.once('exit',r));child.kill();await stopped;
    child=start();
    let restarted=false;
    for(let i=0;i<60;i++){try{if((await fetch(base)).ok){restarted=true;break}}catch{}await new Promise(r=>setTimeout(r,100))}
    assert(restarted,'service did not restart');
    await writer.getByRole('button',{name:'实时协作',exact:true}).click();
    assert.equal(await writer.locator('#collab-body').inputValue(),merged);
    await writer.locator('#collab-label').fill('V0.2');
    await writer.locator('#collab-note').fill('合并两人的并发修改');
    await writer.getByRole('button',{name:'保存为新版本',exact:true}).click();
    await writer.locator('.collab-dialog').waitFor({state:'detached'});
    // Saving a new snapshot invalidates the old editing room, preserving its local text.
    await page.waitForFunction(()=>document.querySelector('#collab-body').readOnly);
    await page.getByRole('button',{name:'同步并关闭',exact:true}).click();
    await page.locator('.collab-dialog').waitFor({state:'detached'});
    // A live change is checkpointed atomically when submitting for administrator review.
    await writer.getByRole('button',{name:'实时协作',exact:true}).click();
    await writer.locator('#collab-body').fill(merged+'\n审核冻结验证。');
    await writer.getByRole('button',{name:'同步并关闭',exact:true}).click();
    await writer.locator('.collab-dialog').waitFor({state:'detached'});
    await writer.getByRole('button',{name:'提交文档',exact:true}).click();
    await writer.getByLabel('审核 / 处理说明').fill('正文和依据已编制完成，请管理员审核。');
    await writer.getByRole('button',{name:'确认',exact:true}).click();
    await writer.locator('dialog').waitFor({state:'hidden'});
    await page.getByRole('button',{name:'标准文档库',exact:false}).click();
    await page.getByRole('link',{name:'机器人与国产操作系统接口兼容性要求',exact:true}).click();
    assert((await page.locator('.body-text').innerText()).includes('审核冻结验证。'));
    await page.getByRole('button',{name:'管理员审核通过',exact:true}).click();
    await page.getByLabel('审核 / 处理说明').fill('管理员审核通过，批准形成正式文件。');
    await page.getByRole('button',{name:'确认',exact:true}).click();
    await page.locator('dialog').waitFor({state:'hidden'});
    await page.getByRole('button',{name:'定时发布',exact:true}).click();
    const future=new Date(Date.now()+3600_000+8*3600_000).toISOString().slice(0,16);
    await page.getByLabel('发布时间（北京时间）').fill(future);
    await page.getByRole('button',{name:'确认',exact:true}).click();
    await page.getByRole('button',{name:'取消发布计划',exact:true}).waitFor();
    fs.mkdirSync(path.join(projectRoot,'data'),{recursive:true});
    await page.screenshot({path:path.join(projectRoot,'data/preview.png'),fullPage:true});
    await page.getByRole('button',{name:'取消发布计划',exact:true}).click();
    await page.getByRole('button',{name:'确认',exact:true}).click();
    await page.getByRole('button',{name:'立即发布',exact:true}).click();
    await page.getByLabel('审核 / 处理说明').fill('按批准版本发布。');
    await page.getByRole('button',{name:'确认',exact:true}).click();
    await page.locator('dialog').waitFor({state:'hidden'});
    await page.getByRole('button',{name:'文档评价',exact:true}).click();
    await page.getByRole('button',{name:'评价正式文档',exact:true}).click();
    await page.getByLabel('评价内容',{exact:true}).fill('结构清晰，适合后续标准验证。');
    await page.getByRole('button',{name:'确认',exact:true}).click();
    await page.getByText('结构清晰，适合后续标准验证。',{exact:true}).waitFor();
    await page.getByRole('button',{name:'文档正文',exact:true}).click();
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'mobile page overflows viewport');
    assert.deepEqual(errors,[]);
    console.log('Browser workflow passed: two-user CRDT collaboration, offline concurrent merge, reconnect, service restart recovery, version checkpoint, stale-room freeze, review checkpoint, administrator approval, scheduled publication, evaluation, mobile layout.');
  }finally{
    if(browser)await browser.close();
    child.kill();
    await new Promise(r=>child.once('exit',r));
    if(path.dirname(temp)===os.tmpdir())fs.rmSync(temp,{recursive:true,force:true});
  }
})().catch(e=>{console.error(e);process.exitCode=1});
