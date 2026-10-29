// Background MCP execution remains visible while the Skills product is paused.
export function setupOperations({api,node}){
 const panel=node('details','panel mcp-extension'),heading=node('summary','','后台任务与结果');
 panel.append(heading,node('p','hint','插话不会取消 MCP 执行。语音会话空闲时自动播报结果，也可在这里查看。'));
 const status=node('p','hint'),list=node('div');status.setAttribute('role','status');panel.append(status,list);
 const refreshButton=node('button','secondary','刷新后台任务');refreshButton.type='button';panel.append(refreshButton);
 document.getElementById('mcp-tool-list').after(panel);
 const names={queued:'等待执行',running:'后台执行中',completed:'已处理',failed:'执行失败',unknown:'结果需核实',cancelled:'已取消',waiting:'等待补充或确认'};
 let loading=false,timer;
 async function refresh(){
  if(loading)return;loading=true;
  try{
   const {operations}=await api('operations');list.replaceChildren();
   const running=operations.filter(job=>['queued','running'].includes(job.status)).length;
   heading.textContent='后台任务与结果'+(running?' · '+running+' 项运行中':'');
   for(const job of operations){
    const row=node('article','mcp-imported-server');row.append(node('h3','',job.label),node('p','tag',names[job.status] || job.status));
    if(job.text)row.append(node('p','skill-result',job.text));
    for(const file of job.artifacts || []){const link=node('a','connection-link','下载 '+file.filename);link.href='/api/artifacts/'+encodeURIComponent(file.artifact_id);row.append(link);}
    if(job.workflow_id && job.status==='waiting'){
     const link=node('a','connection-link','查看任务并补充 / 确认');link.href='#mcp';link.onclick=()=>window.dispatchEvent(new Event('voicehub:workflow-open'));row.append(link);
    }
    if(['queued','running'].includes(job.status)){
     const cancel=node('button','secondary','取消任务');cancel.type='button';cancel.onclick=async()=>{
      cancel.disabled=true;
      try{await api('operations/'+job.id+'/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});await refresh();}
      catch(e){status.textContent=e.message;cancel.disabled=false;}
     };row.append(cancel);
    }else row.append(node('p','hint',job.delivery==='superseded'?'提示已被新的输入替代。':job.delivery==='cancelled'?'提醒通知已取消。':job.session_id.startsWith('ui-')?'页面任务：结果已保存。':job.delivery==='announced'?'结果已播报':'等待原语音会话空闲时播报；断线后可恢复原会话或在这里查看。'));
    list.append(row);
   }
   if(!operations.length)list.append(node('p','hint','暂无后台任务。'));
   status.textContent='';
  }catch(e){status.textContent=e.message;}finally{loading=false;}
 }
 refreshButton.onclick=refresh;
 function schedule(){clearTimeout(timer);timer=setTimeout(refresh,250);}
 return {render:refresh,schedule};
}
