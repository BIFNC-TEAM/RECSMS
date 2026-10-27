import * as Y from 'yjs';

const encode=bytes=>{let s='';for(let i=0;i<bytes.length;i+=8192)s+=String.fromCharCode(...bytes.subarray(i,i+8192));return btoa(s)};
const decode=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));

export async function open({id,version,user,api,onCheckpoint}) {
  const initial=await api(`documents/${id}/collab?version_id=${version}`);
  if(!initial.writable)throw Error('当前版本已冻结，不能协作编辑');
  const doc=new Y.Doc(),text=doc.getText('body');
  Y.applyUpdate(doc,decode(initial.state),'remote');
  const key=`standards-collab:${user}:${id}:${version}`;
  let serial=0,acked=0,revision=initial.revision,busy=null,composing=false,closed=false,frozen=false;
  try{const saved=sessionStorage.getItem(key);if(saved){Y.applyUpdate(doc,decode(saved),'restore');serial++}}catch{}
  const dialog=document.createElement('dialog');
  dialog.className='collab-dialog';
  dialog.innerHTML='<div class="heading"><div><h2>正文实时协作</h2><span class="muted">多人的修改自动合并；保存版本后进入版本记录。</span></div><button id="collab-close">同步并关闭</button></div><div class="collab-toolbar"><span id="collab-status" role="status">正在连接…</span><span id="collab-people"></span><button id="collab-undo">撤销自己的修改</button><button id="collab-export">导出本地正文</button></div><label for="collab-body">协作正文</label><textarea id="collab-body" spellcheck="false"></textarea><div class="row"><label>新版本号<input id="collab-label" maxlength="80" placeholder="例如 V1.1"></label><label style="flex:1">变更说明<input id="collab-note" maxlength="2000" placeholder="填写本次变更说明"></label><button class="primary" id="collab-save">保存为新版本</button></div><p class="muted">正文约每 0.7 秒同步。提交审核会冻结服务端已同步内容；其他窗口尚未同步的内容会保留在本地，可导出。</p>';
  document.body.append(dialog);
  const area=dialog.querySelector('#collab-body'),status=dialog.querySelector('#collab-status');
  const people=dialog.querySelector('#collab-people'),save=dialog.querySelector('#collab-save');
  area.value=text.toString();
  const undo=new Y.UndoManager(text,{trackedOrigins:new Set(['local'])});
  function stash(){try{sessionStorage.setItem(key,encode(Y.encodeStateAsUpdate(doc)))}catch{status.textContent='本地备份空间不足，请保持窗口打开并同步'}}
  doc.on('update',(_update,origin)=>{if(origin!=='remote'){serial++;stash()}});
  function edit(){
    const before=text.toString(),after=area.value;
    let start=0,end=0;
    while(start<before.length&&start<after.length&&before[start]===after[start])start++;
    while(end<before.length-start&&end<after.length-start&&before[before.length-1-end]===after[after.length-1-end])end++;
    doc.transact(()=>{if(before.length-start-end)text.delete(start,before.length-start-end);if(after.length-start-end)text.insert(start,after.slice(start,after.length-end))},'local');
    status.textContent='有修改待同步…';
  }
  area.addEventListener('input',()=>{if(!composing)edit()});
  area.addEventListener('compositionstart',()=>composing=true);
  area.addEventListener('compositionend',()=>{composing=false;edit()});
  function apply(state){
    const anchor=Y.createRelativePositionFromTypeIndex(text,Math.min(area.selectionStart,text.length));
    const head=Y.createRelativePositionFromTypeIndex(text,Math.min(area.selectionEnd,text.length));
    const scroll=area.scrollTop,direction=area.selectionDirection;
    Y.applyUpdate(doc,decode(state),'remote');
    if(area.value!==text.toString()){
      area.value=text.toString();
      const a=Y.createAbsolutePositionFromRelativePosition(anchor,doc),b=Y.createAbsolutePositionFromRelativePosition(head,doc);
      if(a&&b)area.setSelectionRange(a.index,b.index,direction);
      area.scrollTop=scroll;
    }
  }
  const client_id=crypto.randomUUID();
  async function tick(){
    if(closed||frozen||composing)return;
    if(busy)return busy;
    busy=(async()=>{
      const sent=serial;
      try{
        const result=await api(`documents/${id}/collab`,{version_id:version,client_id,...(sent!==acked?{update:encode(Y.encodeStateAsUpdate(doc))}:{})});
        // Do not rewrite a live Chinese IME composition; merge on the next tick instead.
        if(!composing)apply(result.state);
        revision=result.revision;
        acked=sent;
        if(acked===serial){try{sessionStorage.removeItem(key)}catch{}}
        people.textContent='在线协作者：'+(result.people.join('、')||user);
        status.textContent=acked===serial?'已同步至服务端':'有修改待同步…';
      }catch(e){
        status.textContent=e.message+'；本地内容保留';
        if(/归档|冻结|仅草稿|版本已变化|无.*权限|需要起草角色/.test(e.message)){frozen=true;area.readOnly=true;save.disabled=true;clearInterval(timer)}
        throw e;
      }finally{busy=null}
    })();
    return busy;
  }
  async function flush(){
    if(composing)throw Error('请先完成当前输入');
    await tick();
    for(let i=0;acked!==serial&&i<10;i++)await tick();
    if(acked!==serial)throw Error('尚有内容未同步，请稍后重试');
  }
  function unload(e){if(acked!==serial){e.preventDefault();e.returnValue=''}}
  window.addEventListener('beforeunload',unload);
  function dispose(){closed=true;clearInterval(timer);window.removeEventListener('beforeunload',unload);undo.destroy();doc.destroy();dialog.close();dialog.remove()}
  async function close(){try{if(!frozen)await flush();dispose()}catch(e){status.textContent=e.message+'；请重试，或先导出本地正文'}}
  dialog.querySelector('#collab-close').onclick=close;
  dialog.addEventListener('cancel',e=>{e.preventDefault();close()});
  function undoLocal(){undo.undo();area.value=text.toString()}
  dialog.querySelector('#collab-undo').onclick=undoLocal;
  area.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();if(e.shiftKey)undo.redo();else undo.undo();area.value=text.toString()}});
  dialog.querySelector('#collab-export').onclick=()=>{const url=URL.createObjectURL(new Blob([area.value],{type:'text/plain;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=`协作本地稿-${id}.txt`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
  save.onclick=async()=>{
    save.disabled=true;
    try{
      const label=dialog.querySelector('#collab-label').value.trim(),note=dialog.querySelector('#collab-note').value.trim();
      if(!label||!note)throw Error('请填写新版本号和变更说明');
      await flush();
      await api(`documents/${id}/collab-checkpoint`,{expected_version:version,revision,label,note});
      dispose();await onCheckpoint();
    }catch(e){status.textContent=e.message}finally{save.disabled=frozen}
  };
  const timer=setInterval(()=>tick().catch(()=>{}),700);
  dialog.showModal();
  await tick().catch(()=>{});
}
