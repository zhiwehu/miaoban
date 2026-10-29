// Explicitly opt-in user code, library settings, and tool tests. No HTML from server data.
import {setupFileOrganizer} from './file-organizer-ui.js';

export function setupMcpExtensions({api,node,notice,onConfig,refresh}) {
  const $=id=>document.getElementById(id);
  const filesUi=setupFileOrganizer({api,node,onConfig,refreshCatalogue:refresh});
  let musicDirty=false;
  $('music-directory').oninput=()=>musicDirty=true;
  $('music-enabled').onchange=()=>musicDirty=true;
  async function testTool(name,arguments_) {
    const response=await api('mcp/tools/'+encodeURIComponent(name)+'/test',{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(arguments_)});
    const result=response.result;
    if(result.isError)throw new Error(result.structuredContent?.error?.message || result.content?.[0]?.text || '工具调用失败');
    return result;
  }
  $('music-settings-form').onsubmit=async event=>{
    event.preventDefault();const button=event.submitter;button.disabled=true;
    try {
      const {config}=await api('config');
      config.tools.music_directory=$('music-directory').value.trim();
      config.tools.music=$('music-enabled').checked;
      const saved=await api('config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(config)});
      onConfig(saved.config);musicDirty=false;await refresh();
      $('music-status').textContent='音乐设置已保存，重新连接聊天后生效。';
    } catch(e){$('music-status').textContent=e.message;}finally{button.disabled=false;}
  };
  $('music-scan').onclick=async()=>{
    const button=$('music-scan');button.disabled=true;
    $('music-status').textContent='正在扫描音乐目录…';$('music-library').replaceChildren();
    try {
      const result=await testTool('music',{action:'search'});
      const data=JSON.parse(result.content[0].text);
      $('music-status').textContent='找到 '+data.matches+' 首音乐'+(data.truncated?'（扫描已达上限）':'')+'，显示前 20 首。';
      for(const track of data.tracks){
        const row=node('div','connection-device'),button=node('button','secondary','播放');
        button.type='button';button.setAttribute('aria-label','播放 '+track.title);
        button.onclick=async()=>{
          button.disabled=true;
          try{
            const result=await testTool('music',{action:'play',track_id:track.track_id});
            const data=JSON.parse(result.content[0].text);
            if(data.status!=='playing')throw new Error(data.message || '尚未开始播放');
            $('music-status').textContent='已启动 '+track.title+'，输出在后端电脑。';
          }catch(e){$('music-status').textContent=e.message;}finally{button.disabled=false;}
        };
        row.append(node('span','',track.title),button);$('music-library').append(row);
      }
      for(const [action,label] of [['pause','暂停音乐'],['resume','继续播放'],['stop','停止音乐']]){
        const button=node('button','secondary',label);button.type='button';
        button.onclick=async()=>{
          button.disabled=true;
          try{await testTool('music',{action});$('music-status').textContent=label+'请求已完成。';}
          catch(e){$('music-status').textContent=e.message;}finally{button.disabled=false;}
        };
        $('music-library').append(button);
      }
    }catch(e){$('music-status').textContent=e.message;}finally{button.disabled=false;}
  };
  $('mcp-upload-form').onsubmit=async event=>{
    event.preventDefault();const file=$('mcp-upload-file').files[0];if(!file)return;
    if(file.size>2*1024*1024){$('mcp-upload-status').textContent='文件不能超过 2 MB。';return;}
    const button=event.submitter;button.disabled=true;$('mcp-upload-status').textContent='正在检查文件…';
    try {
      await api('mcp/import',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});
      $('mcp-upload-status').textContent='上传成功，尚未运行。请核对来源后启用并验证。';
      $('mcp-upload-file').value='';await refresh();
    }catch(e){$('mcp-upload-status').textContent='上传失败：'+e.message;}finally{button.disabled=false;}
  };
  $('mcp-remote-form').onsubmit=async event=>{
    event.preventDefault();const button=event.submitter;button.disabled=true;
    $('mcp-remote-status').textContent='正在检查并保存配置…';
    try{
      const headers=JSON.parse($('mcp-remote-headers').value.trim() || '{}');
      if(!headers || typeof headers!=='object' || Array.isArray(headers))throw new Error('请求头必须是 JSON 对象。');
      const token=$('mcp-remote-token').value.trim();
      if(token){
        if(Object.keys(headers).some(key=>key.toLowerCase()==='authorization'))throw new Error('Bearer Token 与自定义 Authorization 请只填写一种。');
        headers.Authorization='Bearer '+token;
      }
      const id=$('mcp-remote-id').value.trim();
      await api('mcp/remote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
        id,name:$('mcp-remote-name').value.trim() || id,url:$('mcp-remote-url').value.trim(),headers,
        allow_private:$('mcp-remote-private').checked,protocol_version:$('mcp-remote-version').value})});
      $('mcp-remote-form').reset();await refresh();
      $('mcp-remote-status').textContent='已保存，尚未连接。请在下方确认信任后启用并验证。';
    }catch(e){$('mcp-remote-status').textContent='保存失败：'+e.message;}finally{button.disabled=false;}
  };
  function render(data){
    filesUi.render(data);
    if(!musicDirty){$('music-directory').value=data.music_directory || '';$('music-enabled').checked=data.music_enabled;}
    const target=$('mcp-external-servers');target.replaceChildren();
    const servers=data.servers.filter(s=>s.kind!=='builtin');
    if(!servers.length)target.append(node('p','hint','尚未添加外部 MCP。可连接远程服务，或下载并上传回显示例。'));
    for(const server of servers){
      const box=node('article','mcp-imported-server');
      box.append(node('h3','',server.name),node('p','hint',server.enabled?'已启用并通过工具发现 · '+server.tool_count+' 个工具':
        server.verified?'已停用 · 曾通过工具发现':'尚未启用验证'));
      box.append(node('p','mono',server.id+' · '+(server.transport==='streamable_http'?'Streamable HTTP · '+server.url:server.command)),node('p','hint','SHA256：'+server.sha256));
      if(server.transport==='streamable_http')box.append(node('p','hint','协议 '+server.protocol_version+' · 认证头：'+(server.authentication_headers.join('、') || '无')));
      if(server.error)box.append(node('p','tool-call-error','验证失败：'+server.error+'。请核对地址、认证与协议，或本地依赖。'));
      const trust=node('label','check'),check=node('input');check.type='checkbox';
      trust.append(check,node('span','',server.transport==='streamable_http'?'我信任此远程服务，允许连接并向它发送所选工具的调用参数':'我信任此服务，允许它以当前用户权限运行代码'));
      if(!server.enabled)box.append(trust);
      const actions=node('div','button-row');
      const enable=node('button','primary',server.enabled?'停用服务':'启用并验证');enable.type='button';
      enable.disabled=!server.enabled;check.onchange=()=>enable.disabled=!check.checked;
      enable.onclick=async()=>{
        enable.disabled=true;
        try{
          await api('mcp/servers/'+server.id+'/'+(server.enabled?'disable':'enable'),{
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({trust:check.checked})});
          await refresh();$('mcp-upload-status').textContent=server.enabled?'服务已停用。':'服务已通过 MCP 工具发现验证，可在工具卡片中测试调用。';
        }catch(e){$('mcp-upload-status').textContent='操作失败：'+e.message;await refresh();}
      };
      const remove=node('button','secondary','删除服务');remove.type='button';
      remove.onclick=async()=>{
        remove.disabled=true;
        try{await api('mcp/servers/'+server.id+'/delete',{method:'POST'});await refresh();}
        catch(e){$('mcp-upload-status').textContent=e.message;remove.disabled=false;}
      };
      actions.append(enable,remove);box.append(actions);target.append(box);
    }
  }
  function toolTest(tool){
    const details=node('details','mcp-schema');details.append(node('summary','','手动测试调用'));
    const form=node('form'),label=node('label','','参数 JSON'),input=node('textarea');
    input.rows=4;input.value=JSON.stringify(tool.name==='music'?{action:'search'}:tool.name==='file_organizer'?{action:'preview'}:
      Object.fromEntries((tool.inputSchema.required || []).map(key=>[key,''])),null,2);
    input.setAttribute('aria-label',tool.name+'测试参数');label.append(input);
    const button=node('button','secondary','执行测试');button.type='submit';button.disabled=!tool.enabled;
    const status=node('p','hint');status.setAttribute('role','status');const output=node('pre','mcp-test-output');
    form.onsubmit=async event=>{
      event.preventDefault();button.disabled=true;status.textContent='正在调用 MCP…';output.textContent='';
      try{const result=await testTool(tool.name,JSON.parse(input.value));output.textContent=JSON.stringify(result,null,2);status.textContent='调用成功。';}
      catch(e){status.textContent='调用失败：'+e.message;}finally{button.disabled=!tool.enabled;}
    };
    form.append(label,button,status,output);details.append(form);return details;
  }
  return {render,toolTest};
}
