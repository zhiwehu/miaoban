// Local calendar configuration belongs to its own MCP card.
export function setupCalendar({api,node,notice}){
 const panel=node('article','panel mcp-extension');panel.append(node('h2','','日程与提醒'),node('p','hint','本地日程库，支持单次日程。语音创建会绑定当前设备；页面创建使用下方默认设备。平台运行时到点提醒，插话不会丢失提醒。'));
 const status=node('p','hint');status.setAttribute('role','status');let loaded=false,dirty=false,loading=false,timer;
 const inputs={},form=node('form');
 function field(host,key,title,type='text'){
  const label=node('label','',title),input=node('input');input.type=type;inputs[key]=input;label.append(input);host.append(label);return input;
 }
 field(form,'timezone','日程时区（IANA 名称）').placeholder='Asia/Shanghai';
 field(form,'remind_minutes','默认提前提醒（分钟）','number').min=0;inputs.remind_minutes.max=10080;
 field(form,'duration_minutes','默认日程时长（分钟）','number').min=1;inputs.duration_minutes.max=1440;
 field(form,'late_minutes','离线后的补播窗口（分钟）','number').min=1;inputs.late_minutes.max=1440;
 const device=field(form,'device_id','默认提醒设备（留空只在页面提示）');device.maxLength=80;device.placeholder='选择在线设备或填写 device_id';device.setAttribute('list','calendar-devices');
 const devices=node('datalist');devices.id='calendar-devices';form.append(devices);
 const save=node('button','primary','保存日程设置');save.type='submit';form.append(save);panel.append(form,status);
 form.oninput=()=>dirty=true;
 const create=node('details','mcp-schema');create.append(node('summary','','添加日程 / 提醒'));const createForm=node('form');
 const title=field(createForm,'new_title','事项');title.required=true;title.maxLength=100;
 const start=field(createForm,'new_start','开始时间（按配置时区）','datetime-local');start.required=true;
 const end=field(createForm,'new_end','结束时间（可选）','datetime-local');
 const lead=field(createForm,'new_lead','提前提醒分钟数（留空使用默认值）','number');lead.min=0;lead.max=10080;
 const add=node('button','primary','添加日程');add.type='submit';createForm.append(add);create.append(createForm);panel.append(create);
 const query=node('form'),from=field(query,'range_from','查询开始日期','date'),until=field(query,'range_until','查询结束日期（不含当天）','date');from.required=until.required=true;
 const refreshButton=node('button','secondary','查询日程');refreshButton.type='submit';query.append(refreshButton);panel.append(query);const list=node('div');panel.append(list);
 async function write(path,method,body){return api(path,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}
 form.onsubmit=async e=>{e.preventDefault();save.disabled=true;try{
  await write('calendar/settings','PUT',{timezone:inputs.timezone.value,remind_minutes:Number(inputs.remind_minutes.value),duration_minutes:Number(inputs.duration_minutes.value),late_minutes:Number(inputs.late_minutes.value),device_id:device.value.trim()});dirty=false;loaded=false;status.textContent='日程设置已保存，已有日程保留创建时的时区和设备。';await render();
 }catch(e){status.textContent=e.message;}finally{save.disabled=false;}};
 createForm.onsubmit=async e=>{e.preventDefault();add.disabled=true;try{
  const body={title:title.value,start_time:start.value,request_key:crypto.randomUUID()};if(end.value)body.end_time=end.value;if(lead.value!=='')body.remind_minutes=Number(lead.value);
  await write('calendar/events','POST',body);status.textContent='日程和提醒已添加。';createForm.reset();await render();
 }catch(e){status.textContent=e.message;}finally{add.disabled=false;}};
 query.onsubmit=async e=>{e.preventDefault();await render();};
 async function render(){
  if(loading)return;loading=true;
  try{
   if(!loaded && !dirty){const {settings}=await api('calendar/settings');for(const key of ['timezone','remind_minutes','duration_minutes','late_minutes','device_id'])inputs[key].value=settings[key];
    if(!from.value){const date=new Intl.DateTimeFormat('sv-SE',{timeZone:settings.timezone,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());from.value=date;const endDate=new Date(date+'T12:00:00Z');endDate.setUTCDate(endDate.getUTCDate()+7);until.value=endDate.toISOString().slice(0,10);}
    const state=await api('status');devices.replaceChildren();for(const id of new Set(state.sessions.map(s=>s.device_id))){const option=node('option');option.value=id;devices.append(option);}loaded=true;
   }
   if(!from.value || !until.value)return;
   const data=await api('calendar/events?'+new URLSearchParams({start:from.value+'T00:00',end:until.value+'T00:00'}));list.replaceChildren();
   const labels={pending:'待提醒',due:'到点待提醒',queued:'等待语音播报',announced:'已播报',missed:'已错过补播窗口',page:'已生成页面提醒',muted:'工具已停用，未播报'};
   list.append(node('p','hint','时区：'+data.timezone+' · '+data.count+' 项日程'+(data.truncated?'，显示前20项':'')));
   for(const event of data.events){const box=node('article','mcp-imported-server');box.append(node('h3','',event.title),node('p','',event.start_time.replace('T',' ')+' — '+event.end_time.replace('T',' ')),node('p','hint','提醒：'+event.remind_at.replace('T',' ')+' · '+(labels[event.reminder_status] || event.reminder_status)),node('p','hint',event.device_id?'提醒设备：'+event.device_id:'仅在页面提示'));
    const cancel=node('button','secondary','取消日程');cancel.onclick=async()=>{cancel.disabled=true;try{await write('calendar/events/'+event.event_id+'/cancel','POST',{});status.textContent='日程已取消，未播报的提醒也已取消。';await render();}catch(e){status.textContent=e.message;cancel.disabled=false;}};box.append(cancel);list.append(box);
   }
   if(!data.events.length)list.append(node('p','hint','这个时间范围内没有日程。'));
  }catch(e){status.textContent=e.message;}finally{loading=false;}
 }
 function track(event){if(event.type==='calendar.reminder')notice(event.text);if(event.type==='calendar.error')notice(event.message,true);clearTimeout(timer);timer=setTimeout(render,250);}
 document.getElementById('mcp-tool-settings-storage').append(panel);return {panel,render,track};
}
