// Explicit protocol/UI fixture. This does NOT run or simulate the actual ONLYOFFICE editing engine.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const {spawn,spawnSync}=require('node:child_process');
const http=require('node:http'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const wait=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'office-browser-'));
  const python=process.env.PYTHON_EXECUTABLE||'python',secret='ui-protocol-fixture-secret-at-least-32-characters';
  const fixture=path.join(temp,'edited.docx');
  const made=spawnSync(python,['-c','import onlyoffice,sys;from pathlib import Path;Path(sys.argv[1]).write_bytes(onlyoffice.make_docx("Office 浏览器保存验证"))',fixture],{cwd:__dirname});
  assert.equal(made.status,0,made.stderr.toString());
  const base='http://127.0.0.1:18766';let child,browser,docsURL;const sessions=new Map(),callbackErrors=[];
  function sign(payload){const value=Buffer.from(JSON.stringify({alg:'HS256',typ:'JWT'})).toString('base64url')+'.'+Buffer.from(JSON.stringify({payload,operation:'callback'})).toString('base64url');return value+'.'+crypto.createHmac('sha256',secret).update(value).digest('base64url')}
  const fake=http.createServer(async(req,res)=>{
    try{
      const url=new URL(req.url,docsURL);res.setHeader('Access-Control-Allow-Origin','*');
      if(url.pathname==='/healthcheck'){res.end('true');return}
      if(url.pathname==='/result.docx'){res.end(fs.readFileSync(fixture));return}
      if(url.pathname==='/web-apps/apps/api/documents/api.js'){
        res.setHeader('Content-Type','text/javascript');
        res.end(`window.DocsAPI={DocEditor:function(id,config){const frame=document.createElement('iframe');frame.title='ONLYOFFICE protocol test fixture';frame.style.cssText='width:100%;height:100%';frame.src=${JSON.stringify(docsURL)}+'/editor?key='+encodeURIComponent(config.document.key)+'&callback='+encodeURIComponent(config.editorConfig.callbackUrl)+'&file='+encodeURIComponent(config.document.url)+'&mode='+config.editorConfig.mode;document.getElementById(id).replaceWith(frame);frame.onload=()=>config.events.onDocumentReady();this.destroyEditor=()=>{const signal=document.createElement('script');signal.src=${JSON.stringify(docsURL)}+'/save?key='+encodeURIComponent(config.document.key);document.head.append(signal);frame.remove()}}};`);return
      }
      if(url.pathname==='/editor'){
        sessions.set(url.searchParams.get('key'),Object.fromEntries(url.searchParams));
        const source=await fetch(url.searchParams.get('file'));assert.equal(source.status,200);assert((await source.arrayBuffer()).byteLength>100);
        res.setHeader('Content-Type','text/html;charset=utf-8');res.end('<h2>仅用于协议测试的编辑器占位界面</h2><textarea aria-label="测试正文">测试文件已加载</textarea>');return
      }
      if(url.pathname==='/save'){
        const key=url.searchParams.get('key'),entry=sessions.get(key);
        const payload=entry.mode==='view'?{key,status:4}:{key,status:2,url:docsURL+'/result.docx'};
        const r=await fetch(entry.callback,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+sign(payload)},body:JSON.stringify(payload)});
        if(r.status!==200)callbackErrors.push(await r.text());res.setHeader('Content-Type','text/javascript');res.end('/* callback delivered */');return
      }
      res.statusCode=404;res.end('not found');
    }catch(e){callbackErrors.push(e.message);res.statusCode=500;res.end('fixture error')}
  });
  await new Promise(r=>fake.listen(0,'127.0.0.1',r));docsURL='http://127.0.0.1:'+fake.address().port;
  try{
    child=spawn(python,['-u',path.join(__dirname,'server.py'),'--port','18766'],{env:{...process.env,STANDARD_DB:path.join(temp,'office.db'),STANDARD_ADMIN_PASSWORD:'office-browser-password',ONLYOFFICE_PUBLIC_URL:docsURL,ONLYOFFICE_INTERNAL_URL:docsURL,ONLYOFFICE_PLATFORM_URL:base,ONLYOFFICE_JWT_SECRET:secret},stdio:'ignore',windowsHide:true});
    let ready=false;for(let i=0;i<80;i++){try{if((await fetch(base)).ok){ready=true;break}}catch{}await wait(100)}assert(ready);
    browser=await chromium.launch({headless:true,...(process.env.BROWSER_CHANNEL?{channel:process.env.BROWSER_CHANNEL}:{})});
    const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base);await page.getByLabel('账号',{exact:true}).fill('admin');await page.getByLabel('密码',{exact:true}).fill('office-browser-password');await page.getByRole('button',{name:'登录工作空间'}).click();
    await page.getByRole('button',{name:'管理与审计',exact:false}).click();
    await page.getByRole('button',{name:'ONLYOFFICE 配置',exact:true}).click();
    assert.equal(await page.getByLabel('浏览器访问 Document Server 地址').inputValue(),docsURL);
    assert.equal(await page.getByLabel('JWT 共享密钥（至少 32 位；留空保留原值）').inputValue(),'');
    await page.getByRole('button',{name:'取消',exact:true}).click();
    await page.getByRole('button',{name:'检测 Office 连接',exact:true}).click();
    await page.getByRole('status').filter({hasText:'已连接'}).waitFor();
    await page.getByRole('button',{name:'标准文档库',exact:false}).click();await page.getByRole('button',{name:'新建工作副本',exact:false}).click();
    await page.getByLabel('标准编号',{exact:true}).fill('OO-UI-001');await page.getByLabel('文档名称',{exact:true}).fill('Office 接口浏览器测试');await page.getByLabel('正文',{exact:true}).fill('初始正文');await page.getByRole('button',{name:'确认',exact:true}).click();await page.locator('#modal').waitFor({state:'hidden'});
    await page.getByRole('button',{name:'编辑文档',exact:true}).click();
    await page.getByText('Word 实时协作已连接',{exact:true}).waitFor();
    await page.frameLocator('iframe[title="ONLYOFFICE protocol test fixture"]').getByLabel('测试正文').waitFor();
    await page.getByRole('button',{name:'关闭编辑并等待保存',exact:true}).click();await page.locator('.office-dialog').waitFor({state:'detached'});
    assert.equal(await page.getByRole('button',{name:'保存 Office 新版本',exact:true}).count(),0);
    await page.getByText('Office 浏览器保存验证',{exact:true}).first().waitFor();
    assert.deepEqual(await page.locator('.document-control-buttons button').allTextContents(),['提交文档','分享协作','刷新','删除版本']);
    assert.equal(await page.locator('.editing-status').innerText(),'已完成');fs.mkdirSync(path.join(__dirname,'data'),{recursive:true});await page.screenshot({path:path.join(__dirname,'data/ui-autosave.png'),fullPage:true});
    assert((await page.locator('.body-text').innerText()).includes('Office 浏览器保存验证'));
    assert.equal(await page.getByRole('button',{name:'实时协作',exact:true}).count(),0);
    const downloadPromise=page.waitForEvent('download');await page.getByRole('button',{name:'下载文档',exact:true}).click();assert((await downloadPromise).suggestedFilename().endsWith('.docx'));
    await page.getByRole('button',{name:'版本管理',exact:true}).click();await page.locator('[data-version-row]').first().getByRole('button',{name:'从此版本创建草稿',exact:true}).click();
    await page.getByRole('button',{name:'确认',exact:true}).click();
    await page.locator('#modal').waitFor({state:'hidden'});
    await page.getByRole('heading',{name:'Office 接口浏览器测试（admin独立修改）',exact:true}).waitFor();
    await page.getByRole('button',{name:'版本管理',exact:true}).click();await page.getByRole('button',{name:'对照合并内容',exact:true}).click();
    await page.getByRole('heading',{name:'合并前对照',exact:true}).waitFor();
    assert((await page.locator('#modal-content').innerText()).includes('主文档未偏离基础版本'));
    await page.getByRole('button',{name:'取消',exact:true}).click();
    await page.getByRole('button',{name:'删除版本',exact:true}).click();
    await page.getByRole('button',{name:'确认',exact:true}).click();
    await page.locator('#modal').waitFor({state:'hidden'});
    await page.getByRole('heading',{name:'标准文档库',exact:true}).waitFor();
    assert.equal(await page.getByRole('link',{name:'Office 接口浏览器测试（admin独立修改）',exact:true}).count(),0);
    assert.deepEqual(errors,[]);assert.deepEqual(callbackErrors,[]);
    console.log('Office browser protocol fixture passed: settings, signed file, script/iframe, automatic archival, latest DOCX download, four controls, working-copy deletion and merge preview. Actual ONLYOFFICE engine remains untested by this fixture.');
  }finally{
    if(browser)await browser.close();if(child){const done=new Promise(r=>child.once('exit',r));child.kill();await done}
    await new Promise(r=>fake.close(r));if(path.dirname(temp)===os.tmpdir())fs.rmSync(temp,{recursive:true,force:true});
  }
})().catch(e=>{console.error(e);process.exitCode=1});
