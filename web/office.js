'use strict';
window.StandardsOffice={
  generation:0,
  inlineEditor:null,
  async load(settings){
    if(this.scriptURL&&this.scriptURL!==settings.script_url)throw Error('服务地址已改变，请刷新页面');
    if(!window.DocsAPI){
      if(!this.loading)this.loading=new Promise((resolve,reject)=>{const script=document.createElement('script');script.src=settings.script_url;script.onload=resolve;script.onerror=()=>{this.loading=null;script.remove();reject(Error('无法加载 ONLYOFFICE，请检查文档服务连接'))};document.head.append(script)});
      await this.loading;
    }
    this.scriptURL=settings.script_url;
  },
  unmount(){this.generation++;this.inlineEditor?.destroyEditor();this.inlineEditor=null;},
  async mount({id,version,api}){
    this.unmount();const generation=this.generation,host=document.querySelector('#document-preview'),status=document.querySelector('#preview-status');
    if(!host)return;
    try{
      const settings=await api('documents/'+id+'/office-open',{version_id:version,view:true,working:true});
      if(generation!==this.generation||!host.isConnected)return;
      await this.load(settings);
      if(generation!==this.generation||!host.isConnected)return;
      this.inlineEditor=new window.DocsAPI.DocEditor('document-preview',{...settings.config,events:{onDocumentReady:()=>{if(status.isConnected)status.textContent='只读预览'},onError:e=>{if(status.isConnected)status.textContent='预览失败：'+(e.data?.errorDescription||e.data?.errorCode||'请检查文档服务')}}});
    }catch(e){if(generation===this.generation&&status.isConnected)status.textContent='无法预览：'+e.message;}
  },
  async open({id,version,view=false,comparison=null,api,onClose}) {
    // Destroy the background preview before editing; its read-only room must not retain an old snapshot.
    this.unmount();
    // Load the trusted configured script before creating a blocking editing session.
    const settings=await api('documents/'+id+(comparison?'/office-compare':'/office-open'),comparison||{version_id:version,view,working:view});
    const dialog=document.createElement('dialog');
    dialog.className='office-dialog';
    dialog.innerHTML='<div class="heading"><div><h2>ONLYOFFICE Word 协作</h2><span id="office-status" role="status">正在加载编辑器…</span></div><button id="office-close">关闭编辑并等待保存</button><button id="office-leave" hidden>返回后台等待</button></div><div id="office-container"><div id="office-editor"></div></div><p class="muted">只读查看文档。</p>';
    const fullscreen=document.createElement('button');fullscreen.id='office-fullscreen';fullscreen.textContent='全屏';fullscreen.setAttribute('aria-pressed','false');
    dialog.querySelector('.heading').insertBefore(fullscreen,dialog.querySelector('#office-close'));
    document.body.append(dialog);dialog.showModal();
    const status=dialog.querySelector('#office-status'),close=dialog.querySelector('#office-close'),leave=dialog.querySelector('#office-leave');
    if(settings.writable){close.textContent='关闭编辑';dialog.querySelector('p.muted').textContent='编辑时自动保存至平台，无需退出编辑器。';}
    if(!settings.writable)close.textContent='关闭';
    if(settings.comparison){dialog.querySelector('h2').textContent='修订式比较：'+settings.comparison_labels.join(' → ');dialog.querySelector('p.muted').textContent='比较已保存的两个版本，以修订标记显示差异；原版本保持不变。若提示已有修订将被视为接受，可确认继续，仅影响本次比较视图。'}
    let editor=null,ended=false,timer=null,saveTimer=null,ownsFullscreen=false,closing=false,lastSaved=null,engineDirty=false,editorError='';
    const savedLabel=()=>lastSaved?'上次保存（北京时间） '+new Date(lastSaved).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'等待首次自动保存';
    async function monitorSave(){
      if(ended||closing||!settings.writable)return;
      try{
        const result=await api('documents/'+id+'/office-status');
        if(ended||closing)return;
        lastSaved=result.last_saved_at||lastSaved;
        if(!navigator.onLine)status.textContent='网络已断开 · '+savedLabel();
        else if(editorError)status.textContent=editorError+' · '+savedLabel();
        else if(result.session?.last_error)status.textContent=result.session.last_error+' · '+savedLabel();
        else status.textContent=(engineDirty?'修改同步中 · ':'自动保存已开启 · ')+savedLabel();
      }catch{if(!ended&&!closing)status.textContent='连接中断，保存尚未确认 · '+savedLabel();}
      if(!ended&&!closing)saveTimer=setTimeout(monitorSave,2000);
    }
    function offline(){if(settings.writable&&!closing)status.textContent='网络已断开，尚未同步的修改不能保存 · '+savedLabel();}
    function online(){if(settings.writable&&!closing){clearTimeout(saveTimer);monitorSave();}}
    window.addEventListener('offline',offline);window.addEventListener('online',online);
    function setFullscreen(value){dialog.classList.toggle('is-fullscreen',value);fullscreen.textContent=value?'退出全屏':'全屏';fullscreen.setAttribute('aria-pressed',String(value));}
    function fullscreenChanged(){if(ownsFullscreen&&!document.fullscreenElement){ownsFullscreen=false;setFullscreen(false);}}
    document.addEventListener('fullscreenchange',fullscreenChanged);
    async function exitFullscreen(){setFullscreen(false);if(ownsFullscreen&&document.fullscreenElement)await document.exitFullscreen();ownsFullscreen=false;}
    fullscreen.onclick=async()=>{if(dialog.classList.contains('is-fullscreen')){await exitFullscreen();return;}setFullscreen(true);try{if(document.documentElement.requestFullscreen){await document.documentElement.requestFullscreen();ownsFullscreen=true;}}catch{/* Keep the window-filling layout when browser fullscreen is unavailable. */}};
    async function dispose(){if(ended)return;ended=true;clearTimeout(timer);clearTimeout(saveTimer);window.removeEventListener('offline',offline);window.removeEventListener('online',online);document.removeEventListener('fullscreenchange',fullscreenChanged);await exitFullscreen();editor?.destroyEditor();editor=null;dialog.close();dialog.remove();await onClose()}
    async function check(){
      if(ended)return;
      try{
        const result=await api('documents/'+id+'/office-status');
        if(!result.session||result.session.key!==settings.key||result.session.state==='ready'){await dispose();return}
        status.textContent=result.session.last_error||'等待所有协作者关闭，以及 Document Server 的最终保存回调…';
      }catch(e){status.textContent=e.message}
      timer=setTimeout(check,1500);
    }
    async function stop(){
      if(ended)return;
      closing=true;clearTimeout(saveTimer);
      close.disabled=true;
      editor?.destroyEditor();editor=null;
      if(!settings.writable){await dispose();return}
      dialog.querySelector('#office-container').innerHTML='<div class="empty">编辑器已关闭，正在等待文件返回。可以返回后台等待；保存未完成时系统会阻止提交审核。</div>';
      leave.hidden=false;
      await check();
    }
    close.onclick=stop;leave.onclick=dispose;
    dialog.addEventListener('cancel',e=>{e.preventDefault();if(dialog.classList.contains('is-fullscreen'))exitFullscreen();else stop()});
    try{
      await this.load(settings);
      if(ended)return;
      editor=new window.DocsAPI.DocEditor('office-editor',{...settings.config,events:{onDocumentReady:()=>{editorError='';if(settings.comparison){try{editor.setRequestedDocument(settings.comparison);status.textContent='修订式比较已启动，可在文档中查看差异';}catch(e){status.textContent='无法启动修订式比较：'+e.message}}else if(settings.writable){status.textContent='自动保存已开启';monitorSave();}else status.textContent='当前版本只读预览'},onDocumentStateChange:e=>{engineDirty=Boolean(e.data);if(settings.writable&&!closing&&engineDirty)status.textContent='修改同步中 · '+savedLabel();},onError:e=>{editorError='ONLYOFFICE 错误：'+(e.data?.errorDescription||e.data?.errorCode||'请检查服务日志');status.textContent=editorError;}}});
    }catch(e){status.textContent=e.message;leave.hidden=false;close.disabled=true}
  }
};
