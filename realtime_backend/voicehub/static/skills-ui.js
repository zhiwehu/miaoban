// Skills are validated task templates; editing never changes a running snapshot.
export function setupSkills({api,node}){
 const $=id=>document.getElementById(id),status=$('skills-status');let timer,loading=false;
 const names={queued:'等待执行',running:'后台执行中',completed:'已处理',failed:'执行失败',unknown:'结果需核实',cancelled:'已取消',waiting:'等待补充或确认'};
 async function write(path,method,body){return api(path,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}
 function field(form,title,value,type='input'){
  const label=node('label','',title),input=node(type);input.value=value;if(type==='textarea')input.rows=4;label.append(input);form.append(label);return input;
 }
 function card(item){
  const box=node('article','panel mcp-tool'),head=node('div','section-heading'),toggle=node('input');toggle.type='checkbox';toggle.checked=item.enabled;
  const label=node('label','check');toggle.setAttribute('aria-label',item.name+'启用');label.append(toggle,node('span','',item.enabled?'已启用':'已停用'));head.append(node('h2','',item.name),label);box.append(head,node('p','',item.description),node('p','hint','试着说：“'+item.triggers[0]+'”'));
  toggle.onchange=async()=>{toggle.disabled=true;try{await write('skills/'+item.id,'PUT',{...item,enabled:toggle.checked});await refresh(true);}catch(e){toggle.checked=item.enabled;status.textContent=e.message;toggle.disabled=false;}};
  const run=node('details','mcp-tool-details');run.append(node('summary','','运行技能'));const runForm=node('form'),values={};
  for(const [key,spec] of Object.entries(item.parameters.properties)){
   const title=spec.description || ({title:'标题',content:'正文',to:'收件人邮箱'}[key] || key);
   const input=field(runForm,title,spec.default ?? '',spec.enum && spec.type!=='boolean'?'select':key==='content'?'textarea':'input');input.required=(item.parameters.required || []).includes(key);
   if(spec.enum && spec.type!=='boolean'){for(const value of spec.enum){const option=node('option','',String(value));option.value=String(value);input.append(option);}input.value=String(spec.default ?? spec.enum[0]);}
   if(spec.type==='boolean'){input.type='checkbox';input.checked=spec.default || false;}
   else if(!spec.enum && ['integer','number'].includes(spec.type))input.type='number';
   else if(!spec.enum && spec.format==='email')input.type='email';
   if(spec.maxLength)input.maxLength=spec.maxLength;values[key]={input,spec};
  }
  const start=node('button','primary','开始执行');start.type='submit';start.disabled=!item.enabled;runForm.append(start);run.append(runForm);box.append(run);
  runForm.onsubmit=async e=>{e.preventDefault();start.disabled=true;
   try{const args={};for(const [key,{input,spec}] of Object.entries(values))if(input.value!=='' || spec.type==='boolean')args[key]=spec.type==='boolean'?input.checked:['number','integer'].includes(spec.type)?Number(input.value):input.value;
    await write('skills/'+item.id+'/run','POST',{arguments:args});status.textContent='技能已进入后台。页面运行结果在下方查看；语音触发会在原对话自动播报。';await refresh();
   }catch(e){status.textContent=e.message;}finally{start.disabled=!item.enabled;}
  };
  const edit=node('details','mcp-tool-details');edit.append(node('summary','','配置技能'));const form=node('form');
  const title=field(form,'技能名称',item.name),description=field(form,'用途说明',item.description),triggers=field(form,'触发语（每行一句）',item.triggers.join('\n'),'textarea');triggers.rows=2;
  const advanced=node('details','mcp-schema');advanced.append(node('summary','','高级：输入参数与工具步骤'));const definitions=node('div');
  const schema=field(definitions,'输入参数 JSON Schema',JSON.stringify(item.parameters,null,2),'textarea'),steps=field(definitions,'工具步骤 JSON',JSON.stringify(item.steps,null,2),'textarea');steps.rows=8;advanced.append(definitions,node('p','hint','最多 8 步。输入使用 {"input":"参数名"}，前步结果使用 {"from":"步骤ID.字段"}。邮件发送仍需用户确认。'));form.append(advanced);
  const save=node('button','secondary','保存技能');save.type='submit';form.append(save);edit.append(form);box.append(edit);
  form.onsubmit=async e=>{e.preventDefault();save.disabled=true;try{await write('skills/'+item.id,'PUT',{...item,name:title.value,description:description.value,triggers:triggers.value.split('\n').map(v=>v.trim()).filter(Boolean),parameters:JSON.parse(schema.value),steps:JSON.parse(steps.value)});status.textContent='技能已保存，正在执行的任务使用原版本。';await refresh(true);}catch(e){status.textContent=e.message;}finally{save.disabled=false;}};
  return box;
 }
 async function refresh(force=false){
  if(loading)return;loading=true;
  try{
   const [{skills},{operations}]=await Promise.all([api('skills'),api('operations')]);
   // Preserve edits while background status changes arrive.
   const opened=$('skills-list').querySelector('details[open]');if(force || !opened)$('skills-list').replaceChildren(...skills.map(card));
   const target=$('skills-operations');target.replaceChildren();
   for(const job of operations){
    const row=node('article','mcp-imported-server');row.append(node('h3','',job.label),node('p','tag',names[job.status] || job.status));
    if(job.text)row.append(node('p','skill-result',job.text));
    for(const file of job.artifacts || []){const link=node('a','connection-link','下载 '+file.filename);link.href='/api/artifacts/'+encodeURIComponent(file.artifact_id);row.append(link);}
    if(job.workflow_id && job.status==='waiting'){const link=node('a','connection-link','查看任务并补充 / 确认');link.href='#mcp';link.onclick=()=>window.dispatchEvent(new Event('voicehub:workflow-open'));row.append(link);}
    if(['queued','running'].includes(job.status)){const cancel=node('button','secondary','取消任务');cancel.onclick=async()=>{cancel.disabled=true;try{await write('operations/'+job.id+'/cancel','POST',{});await refresh();}catch(e){status.textContent=e.message;}};row.append(cancel);}
    else row.append(node('p','hint',job.session_id.startsWith('ui-')?'页面任务：结果已保存。':job.delivery==='announced'?'结果已播报':'等待原语音会话空闲时播报；断线后可恢复原会话或在这里查看。'));
    target.append(row);
   }
   if(!operations.length)target.append(node('p','hint','暂无后台任务。语音调用的技能与 MCP 会在这里显示执行情况。'));
  }catch(e){status.textContent=e.message;}finally{loading=false;}
 }
 $('skills-refresh').onclick=()=>refresh(true);
 $('skill-create-form').onsubmit=async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{
  await write('skills','POST',{id:$('skill-new-id').value,name:$('skill-new-name').value,description:$('skill-new-description').value,triggers:[$('skill-new-trigger').value],enabled:false,parameters:JSON.parse($('skill-new-parameters').value),steps:JSON.parse($('skill-new-steps').value)});
  status.textContent='技能已创建，默认停用。请检查配置后启用。';$('skill-create-form').reset();await refresh();
 }catch(e){status.textContent=e.message;}finally{button.disabled=false;}};
 window.addEventListener('voicehub:view',e=>{if(e.detail==='skills')void refresh();});
 function track(e){if(e.type==='operation.changed'){clearTimeout(timer);timer=setTimeout(refresh,250);}}
 return {refresh,track};
}
