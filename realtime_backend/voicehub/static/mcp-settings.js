// MCP configuration lives here, separate from model/voice form state.
export function createMcpSettings({api,node,notice}) {
 let tools,globalDirty=false;
 const definitions={
  weather:[['default_city','默认城市（可选）','text']],
  news:[['news_source','默认新闻源',[['chinanews','中新网（中文）'],['bbc','BBC World（英文）']]],['news_limit','默认条数','number',1,5]],
  global:[['planner','工具决策方式',[['llm','对话模型规划'],['rex','本地 Rex 与受限参数提取']]],['timeout_seconds','工具查询超时（秒）','number',5,600]]
 };
 function formFor(name){
  const form=node('form','mcp-settings-form'),fields={};
  for(const [key,title,type,min,max] of definitions[name]){
   const label=node('label','',title),input=node(Array.isArray(type)?'select':'input');
   if(Array.isArray(type))for(const [value,text] of type){const option=node('option','',text);option.value=value;input.append(option);}
   else {input.type=type;if(type==='number'){input.min=min;input.max=max;input.required=true;}else input.maxLength=80;}
   input.value=tools[key];input.setAttribute('aria-label',title);fields[key]=input;label.append(input);form.append(label);
  }
  const button=node('button','secondary',name==='global'?'保存工具通用设置':'保存工具配置');button.type='submit';form.append(button);
  form.oninput=()=>{if(name==='global')globalDirty=true;};
  form.onsubmit=async event=>{event.preventDefault();button.disabled=true;try{
   const {config}=await api('config/tools');
   for(const [key,input] of Object.entries(fields))config.tools[key]=input.type==='number'?Number(input.value):input.value.trim();
   const saved=await api('config/tools',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(config)});
   tools=saved.config.tools;if(name==='global')globalDirty=false;
   notice('工具配置已保存，重新连接客户端后生效。');
  }catch(e){notice(e.message,true);}finally{button.disabled=false;}};
  return form;
 }
 return {
  async load(){tools=(await api('config/tools')).config.tools;const target=document.getElementById('mcp-common-settings');if(!globalDirty){target.replaceChildren(node('h2','','工具通用设置'),formFor('global'));}},
  tool(name){return definitions[name]?formFor(name):null;}
 };
}
