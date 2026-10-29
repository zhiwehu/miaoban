// Task state and SMTP credentials are managed by the host, never by the planner.
const states={planning:'正在规划',ready:'准备执行',running:'正在执行',pending:'待执行',completed:'已完成',waiting_input:'需要补充信息',waiting_configuration:'需要配置 SMTP',waiting_confirmation:'等待确认',failed:'执行失败',interrupted:'执行中断',cancelled:'已取消',unknown:'结果无法确认'};
export function setupWorkflows({api,node}) {
  const host=document.getElementById('mcp-tool-list'),panel=node('details','panel mcp-extension mcp-workflows');
  host.after(panel);panel.append(node('summary','','任务链与执行记录'),node('p','hint','用自然语言组合已注册工具。生成文件后可准备邮件草稿，核对收件人、正文和附件后再发送。'));
  const status=node('p','hint');status.setAttribute('role','status');
  const form=node('form'),label=node('label','','任务目标'),goal=node('textarea');goal.rows=3;goal.maxLength=2000;goal.required=true;goal.placeholder='例如：生成一份 Markdown 会议纪要，发给 demo@example.com';label.append(goal);
  const start=node('button','primary','开始任务');start.type='submit';form.append(label,start);panel.append(form,status);
  const tasks=node('div');panel.append(tasks);
  const settings=node('details','mcp-schema mcp-email-settings');settings.append(node('summary','','SMTP 设置与联系人'));
  const settingsForm=node('form'),inputs={};
  function field(key,title,type='text') {
    const label=node('label','',title),input=node(type==='select'?'select':type==='textarea'?'textarea':'input');
    if(!['select','textarea'].includes(type))input.type=type;
    if(type==='textarea')input.rows=4;
    if(type==='password'){input.autocomplete='new-password';input.placeholder='留空保留已保存的授权码';}
    inputs[key]=input;label.append(input);settingsForm.append(label);return input;
  }
  const taskEnabled=field('enabled','启用自然语言任务链','checkbox');panel.append(taskEnabled.parentElement);
  taskEnabled.onchange=async()=>{try{await api('workflow-settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:taskEnabled.checked})});status.textContent='任务链设置已保存。';}catch(e){taskEnabled.checked=!taskEnabled.checked;status.textContent=e.message;}};
  field('smtp_enabled','启用 SMTP 发送','checkbox');
  field('host','SMTP 服务器');field('port','端口','number');
  const security=field('security','连接加密','select');
  for(const [value,title] of [['ssl','SSL/TLS（通常为 465）'],['starttls','STARTTLS（通常为 587）']]){const opt=node('option','',title);opt.value=value;security.append(opt);}
  field('username','登录账号');field('from_address','发件人邮箱','email');field('password','SMTP 授权码 / 密码','password');field('clear_password','清除已保存的授权码','checkbox');field('password_env','授权码环境变量（可选替代本地保存）');
  field('contacts','联系人 JSON，例如 [{"name":"张三","email":"demo@example.com"}]','textarea');
  const save=node('button','primary','保存设置');save.type='submit';settingsForm.append(save);
  settings.append(settingsForm,node('p','hint','授权码仅保存在后端私有文件或环境变量，不回填页面，也不传给模型。必须启用 TLS；保存设置不会发送邮件。'));
  const download=node('a','connection-link','下载独立邮件 MCP 配置');download.href='/api/workflow-settings/mcp-config';download.download='voicehub-email-mcp.json';settings.append(download);settings.open=true;document.getElementById('mcp-tool-settings-storage').append(settings);
  const emailCard=node('article','panel mcp-tool'),heading=node('div','section-heading'),emailTitle=node('div'),mailState=node('span','tag','尚未配置');
  emailTitle.append(node('h2','','发送邮件'),node('small','mono','voicehub-email'));heading.append(emailTitle,mailState);
  const emailDetails=node('details','mcp-tool-details');emailDetails.append(node('summary','','配置邮件'));
  emailDetails.append(settings);emailCard.append(heading,node('p','','准备邮件草稿和附件，确认后通过 SMTP 发送。'),emailDetails);
  const mailStatus=node('p','hint');mailStatus.setAttribute('role','status');settings.append(mailStatus);
  let loaded=false,dirty=false,timer,loading=false,again=false;
  settingsForm.oninput=()=>dirty=true;
  async function request(path,body){return api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}
  form.onsubmit=async event=>{
    event.preventDefault();start.disabled=true;status.textContent='正在创建任务…';
    try{await request('workflows',{goal:goal.value});status.textContent='任务已创建，正在规划步骤。';await refresh();}
    catch(e){status.textContent=e.message;}finally{start.disabled=false;}
  };
  settingsForm.onsubmit=async event=>{
    event.preventDefault();save.disabled=true;
    try{
      const value=Object.fromEntries(Object.entries(inputs).map(([key,input])=>[key,input.type==='checkbox'?input.checked:input.value]));
      value.port=Number(value.port);value.contacts=JSON.parse(value.contacts || '[]');
      await api('workflow-settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
      dirty=false;loaded=false;inputs.password.value='';mailStatus.textContent='邮件设置已保存。';status.textContent='设置已保存。等待配置的任务可点击“继续任务”。';await refresh();
    }catch(e){mailStatus.textContent=e.message;}finally{save.disabled=false;}
  };
  async function action(task,action,extra={}){
    try{await request('workflows/'+task.id+'/actions',{action,revision:task.revision,...extra});status.textContent='任务状态已更新。';}
    catch(e){status.textContent=e.message;}
    await refresh();
  }
  function renderTask(task){
    const box=node('article','mcp-imported-server');box.append(node('h3','',task.goal),node('p','tag',states[task.status] || task.status));
    for(const step of task.steps){
      box.append(node('p','',step.id+' · '+step.tool+' · '+(states[step.status] || step.status)));
      if(step.result && step.tool!=='document.create'){const details=node('details','mcp-schema');details.append(node('summary','','查看步骤结果'),node('pre','mcp-test-output',JSON.stringify(step.result,null,2)));box.append(details);}
      if(step.tool==='document.create' && step.result){const link=node('a','connection-link','下载 '+step.result.filename);link.href='/api/artifacts/'+encodeURIComponent(step.result.artifact_id);box.append(link);}
    }
    if(task.error)box.append(node('p','tool-call-error','错误：'+task.error));
    if(task.confirmation){
      const summary=task.confirmation.summary,details=node('details','mcp-schema');details.open=true;details.append(node('summary','','执行前核对'));
      if(task.confirmation.tool==='email.send'){
        for(const [key,title] of [['from_address','发件人'],['to','收件人'],['subject','主题'],['body','正文']])details.append(node('p','',title+'：'+(summary[key] || '尚未设置')));
        for(const file of summary.attachments){const link=node('a','connection-link','附件：'+file.filename);link.href='/api/artifacts/'+encodeURIComponent(file.artifact_id);details.append(link);}
        if(!summary.attachments.length)details.append(node('p','hint','无附件'));
      }else details.append(node('pre','mcp-test-output',JSON.stringify(summary,null,2)));
      box.append(details);
    }
    const buttons=node('div','button-row');
    if(task.status==='waiting_confirmation'){
      const label=node('label','check'),check=node('input');check.type='checkbox';label.append(check,node('span','','已核对本次操作和邮件完整内容'));
      const confirm=node('button','primary',task.confirmation.tool==='email.send'?'确认发送邮件':'确认执行');confirm.disabled=true;check.onchange=()=>confirm.disabled=!check.checked;
      confirm.onclick=()=>{confirm.disabled=true;action(task,'confirm',{confirm:true});};box.append(label);buttons.append(confirm);
    }
    if(task.status==='waiting_input'){
      const answerForm=node('form'),label=node('label','',task.question),input=node('input');input.required=true;input.maxLength=2000;label.append(input);
      const answer=node('button','secondary','补充并继续');answer.type='submit';answerForm.append(label,answer);answerForm.onsubmit=e=>{e.preventDefault();answer.disabled=true;action(task,'answer',{answer:input.value});};box.append(answerForm);
    }
    if(['waiting_configuration','failed','interrupted'].includes(task.status)){const resume=node('button','secondary','继续任务');resume.onclick=()=>{resume.disabled=true;action(task,'resume');};buttons.append(resume);}
    if(!['completed','unknown','cancelled'].includes(task.status)){const cancel=node('button','secondary','取消任务');cancel.onclick=()=>{cancel.disabled=true;action(task,'cancel');};buttons.append(cancel);}
    if(task.status==='unknown')box.append(node('p','hint','操作结果不明，已停止自动重试。发送邮件请先核实发件记录，避免重复发送。'));
    box.append(buttons);return box;
  }
  async function refresh(){
    if(loading){again=true;return;}loading=true;
    try{
      if(!loaded && !dirty){const {settings:cfg}=await api('workflow-settings');mailState.textContent=cfg.smtp_ready?'已配置 · '+(cfg.smtp_enabled?'已启用':'已停用'):'需要配置 SMTP';for(const [key,input] of Object.entries(inputs)){
        if(key==='password'){input.value='';input.placeholder=cfg.password_set?'已设置；留空保留':'请输入邮箱的 SMTP 授权码';}
        else if(key==='clear_password')input.checked=false;
        else if(input.type==='checkbox')input.checked=cfg[key];
        else input.value=key==='contacts'?JSON.stringify(cfg.contacts,null,2):cfg[key];
      }loaded=true;}
      const {tasks:rows}=await api('workflows');if(rows.length && (/任务已创建|最近任务/.test(status.textContent)))status.textContent='最近任务：'+(states[rows[0].status] || rows[0].status);tasks.replaceChildren(...rows.map(renderTask));if(!rows.length)tasks.append(node('p','hint','暂无任务。可在这里输入目标，或在语音对话中提出任务。'));
    }catch(e){status.textContent='读取任务失败：'+e.message;}finally{loading=false;if(again){again=false;schedule();}}
  }
  function schedule(){clearTimeout(timer);timer=setTimeout(refresh,250);}
  return {render:refresh,schedule,emailCard};
}
