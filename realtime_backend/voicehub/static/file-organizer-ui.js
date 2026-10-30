// User confirms exact previews here; file mutation is not exposed to model tools.
export function setupFileOrganizer({api,node,onConfig,refreshCatalogue}) {
  const $=id=>document.getElementById(id);let dirty=false,busy=false,enabled=false;
  $('file-directory').oninput=()=>dirty=true;$('file-enabled').onchange=()=>dirty=true;
  const labels={pending:'待确认（十分钟内有效）',executing:'执行中',completed:'整理完成',partial:'部分操作未完成，请查看并按需撤销',undoing:'撤销中',reverted:'已撤销'};
  function planView(plan,isOperation){
    const box=node('article','mcp-imported-server');
    box.append(node('h3','',isOperation?'最近一次整理':'待确认整理方案'),
      node('p','hint',(labels[plan.status] || plan.status)+' · '+plan.count+' 个文件 · 跳过 '+plan.skipped+' 项'),
      node('p','mono',plan.root),node('p','hint',Object.entries(plan.categories).map(([k,v])=>k+' '+v+' 个').join('、')));
    if(plan.error)box.append(node('p','tool-call-error',plan.error==='file_undo_conflict'?'撤销遇到文件变化或重名，现有文件未被覆盖。':'操作未完整结束，记录已保留，可尝试撤销。'));
    const list=node('div','file-plan-list');list.setAttribute('aria-label','文件移动清单');
    for(const file of plan.files){
      const row=node('div','file-plan-row');
      row.append(node('span','',file.source),node('span','','→ '+file.destination+(file.undone?'（已还原）':file.moved?'（已移动）':'')));
      list.append(row);
    }
    box.append(list);
    if(!isOperation && plan.status==='pending' && plan.count){
      const label=node('label','check'),check=node('input');check.type='checkbox';
      label.append(check,node('span','','我已核对以上文件和目标，确认按此方案移动'));
      const button=node('button','primary','确认执行整理');button.type='button';button.disabled=true;
      check.onchange=()=>button.disabled=!check.checked || !enabled || busy;
      button.onclick=()=>mutate('execute',plan.id,button);box.append(label,button);
    }
    if(isOperation && plan.status!=='reverted'){
      const label=node('label','check'),check=node('input');check.type='checkbox';
      label.append(check,node('span','','确认将本次移动的文件还原到原位置'));
      const button=node('button','secondary','撤销这次整理');button.type='button';button.disabled=true;
      check.onchange=()=>button.disabled=!check.checked || !enabled || busy;
      button.onclick=()=>mutate('undo',plan.id,button);box.append(label,button);
    }
    return box;
  }
  async function refresh(){
    try{
      const data=await api('files');enabled=data.enabled;$('file-preview').disabled=!enabled || busy;
      const target=$('file-plans');target.replaceChildren();
      if(data.pending)target.append(planView(data.pending,false));
      if(data.operation)target.append(planView(data.operation,true));
      if(!data.pending && !data.operation)target.append(node('p','hint','还没有整理方案。先设置目录，然后生成预览。'));
    }catch(e){$('file-status').textContent=e.message;}
  }
  async function mutate(action,id,button){
    if(busy)return;busy=true;button.disabled=true;
    $('file-status').textContent=action==='preview'?'正在扫描并生成预览，不会移动文件…':action==='execute'?'正在执行已确认的整理方案…':'正在还原文件…';
    try{
      const data=await api('files/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(action==='preview'?{}:{plan_id:id,confirm:true})});
      $('file-status').textContent=action==='preview'?'预览已生成，文件尚未移动，请核对清单后确认。':labels[data.result.status] || '操作完成。';
    }catch(e){$('file-status').textContent='操作未完成：'+e.message;}
    finally{busy=false;button.disabled=false;await refresh();}
  }
  $('file-settings-form').onsubmit=async event=>{
    event.preventDefault();if(busy)return;const button=event.submitter;button.disabled=true;
    try{
      const {config}=await api('config');config.tools.file_directory=$('file-directory').value.trim();config.tools.file_organizer=$('file-enabled').checked;
      const saved=await api('config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(config)});
      onConfig(saved.config);enabled=saved.config.tools.file_organizer;dirty=false;
      $('file-status').textContent='文件整理设置已保存。语音使用请重新连接聊天；目录变化后需重新生成预览。';await refreshCatalogue();await refresh();
    }catch(e){$('file-status').textContent=e.message;}finally{button.disabled=false;}
  };
  $('file-preview').onclick=event=>mutate('preview',null,event.currentTarget);
  $('file-refresh').onclick=()=>{$('file-status').textContent='';return refresh();};
  function render(data){
    enabled=!!data.file_organizer_enabled;
    if(!dirty){$('file-directory').value=data.file_directory || '';$('file-enabled').checked=enabled;}
    $('file-preview').disabled=!enabled || busy;refresh();
  }
  return {render};
}
