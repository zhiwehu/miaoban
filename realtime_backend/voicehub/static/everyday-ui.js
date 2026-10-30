// Everyday tools stay inside their own MCP cards; text-only rendering of user records.
export function setupEveryday({api,node,notice}) {
 const panels={};
 const specs={shopping_list:{title:'购物清单',hint:'商品保留数量；标记已买不会删除条目。',actions:{list:'查看清单',add:'添加商品',complete:'标记已买',remove:'移除商品'},fields:{items:'商品（每行一个，保留数量）',list_name:'清单名称（默认购物清单）'}},timer:{title:'多计时器',hint:'1秒至24小时。语音创建绑定当前设备，到点自动播报；插话不取消计时。页面创建只在后台结果中提示。暂停不会自动继续。',actions:{status:'查看计时器',start:'新建倒计时',pause:'暂停',resume:'继续',cancel:'取消'},fields:{name:'名称（多个计时器请区分）',duration_seconds:'时长（秒，1—86400）'}},item_memory:{title:'物品位置记录',hint:'只保存你明确提供的位置。查询不到会如实说明。记录保存在本机。',actions:{list:'查看位置记录',remember:'记录位置',recall:'查询位置',forget:'忘记记录'},fields:{item:'物品名称',location:'放置位置'}}};
 for(const [tool,spec] of Object.entries(specs)){
  const panel=node('section','everyday-settings');panel.append(node('p','hint',spec.hint));
  const form=node('form'),action=node('select'),label=node('label','','操作');action.setAttribute('aria-label',spec.title+'操作');
  for(const [key,title] of Object.entries(spec.actions)){const option=node('option','',title);option.value=key;action.append(option);}
  label.append(action);form.append(label);const inputs={};
  for(const [key,title] of Object.entries(spec.fields)){
   const label=node('label','',title),input=node(key==='items'?'textarea':'input');input.setAttribute('aria-label',title);if(key==='duration_seconds'){input.type='number';input.min=1;input.max=86400;}else input.maxLength=key==='location'?300:key==='items'?609:key==='list_name'?40:60;
   label.append(input);inputs[key]=input;form.append(label);
  }
  function updateFields(){const allowed={shopping_list:{list:['list_name'],add:['items','list_name'],complete:['items','list_name'],remove:['items','list_name']},item_memory:{list:[],remember:['item','location'],recall:['item'],forget:['item']},timer:{status:['name'],start:['name','duration_seconds'],pause:['name'],resume:['name'],cancel:['name']}}[tool][action.value];for(const [key,input] of Object.entries(inputs)){input.disabled=!allowed.includes(key);input.parentElement.hidden=input.disabled;}}
  action.onchange=updateFields;updateFields();
  const button=node('button','primary','执行'),output=node('pre','everyday-result');button.type='submit';output.setAttribute('aria-live','polite');form.append(button);panel.append(form,output);
  form.onsubmit=async event=>{event.preventDefault();button.disabled=true;output.textContent='正在处理…';const args={action:action.value};
   for(const [key,input] of Object.entries(inputs)){const value=input.value.trim();if(value&&!input.disabled)args[key]=key==='items'?value.split('\n').map(x=>x.trim()).filter(Boolean):key==='duration_seconds'?Number(value):value;}
   try{const response=await api('mcp/tools/'+tool+'/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(args)});const result=response.result;if(result.isError)throw new Error(result.content?.[0]?.text||'操作未成功');const value=JSON.parse(result.content[0].text);output.textContent=value.status==='needs_selection'?'请说明完整名称，避免重名。':value.status==='not_found'?'没有找到对应记录。':value.items?.length?value.items.map(row=>tool==='shopping_list'?row.name+' · '+(row.state==='bought'?'已买好':row.state==='removed'?'已移除':'待购买'):tool==='item_memory'?row.item+'：'+row.location:row.name+' · '+(row.state==='cancelled'?'已取消':row.state==='paused'?'已暂停，':'')+(row.state==='cancelled'?'':'剩余 '+row.remaining_seconds+' 秒')).join('\n')+(value.truncated?'\n仅显示前 '+value.items.length+' 条，请按名称查询其余记录。':''):'暂无记录。';}
   catch(error){output.textContent=error.message;notice(error.message,true);}finally{button.disabled=false;}
  };
  panels[tool]=panel;
 }
 return panels;
}
