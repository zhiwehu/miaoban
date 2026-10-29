import {normalizeToolEvent, toolCallsForTurn} from './mcp-calls.mjs';
import {setupMcpExtensions} from './mcp-extensions-ui.js';

const names = {calendar:'日程与提醒',weather:'天气查询', news:'新闻摘要', system_info:'系统信息',music:'本地音乐',file_organizer:'文件整理'};
const descriptions={calendar:'查询本地日程，设置一次性提醒，到点自动播报。',weather:'查询指定城市的天气和未来三天预报。',news:'读取新闻标题，可按来源和关键词筛选。',system_info:'查看本机 CPU、内存、磁盘和运行时长。',music:'播放指定目录中的本地音乐，支持暂停和继续。',file_organizer:'按类型整理指定目录，核对方案后执行，支持撤销。'};
const examples = {calendar:'明天有什么日程 / 明天下午三点提醒我开会',weather:'查一下上海明天的天气', news:'从中新网读两条新闻', system_info:'查询本机CPU和内存',music:'播放音乐 / 暂停音乐 / 播放《歌名》',file_organizer:'整理一下指定目录里的文件'};
const phases = {running:'正在调用', completed:'调用成功', failed:'调用失败', cancelled:'已取消', unknown:'状态未确认'};
const fields = {city:'城市',day_offset:'日期偏移',source:'新闻来源',limit:'条数',keyword:'关键词',sections:'查询项',action:'操作',query:'歌名关键词',track_id:'歌曲标识'};
const errors = {upstream_timeout:'上游服务响应超时，请稍后重试。',tool_timeout:'工具调用超时，请稍后重试。',
  upstream_connection_failed:'无法连接上游服务，请检查网络。',tool_unavailable:'工具暂时不可用。',
  tool_invalid_data:'工具返回的数据或参数无效。',
  weather_geocoding_timeout:'城市定位服务连接超时，请稍后重试。',
  weather_forecast_timeout:'天气预报服务连接超时，请稍后重试。',
  weather_geocoding_connection_failed:'暂时无法连接城市定位服务。',
  weather_forecast_connection_failed:'暂时无法连接天气预报服务。',
  weather_geocoding_unavailable:'城市定位服务暂时不可用。',
  weather_forecast_unavailable:'天气预报服务暂时不可用。',
  weather_certificate_error:'天气服务证书验证失败，请检查后端证书和网络。',
  weather_rate_limited:'天气服务请求过于频繁，请稍后再查。',
  weather_service_error:'天气服务返回异常，本次未取得天气数据。',
  weather_invalid_data:'天气服务数据格式异常。',weather_timeout:'天气查询在限定时间内未完成。'};
const duration = ms => ms < 1000 ? Math.round(ms)+' ms' : (ms/1000).toFixed(1)+' 秒';

export function createMcpUi({api, node, notice, onConfig}) {
  const events = new Map(), active = new Map(), revisions = new Map();
  const expanded = new Set();
  let catalogue, saving = false;
  const $ = id => document.getElementById(id);
  const extensions=setupMcpExtensions({api,node,notice,onConfig,refresh:()=>refreshCatalogue()});
  function track(event, live = true) {
    if(event.type.startsWith('calendar.')){extensions.trackCalendar(event);return;}
    if(event.type==='workflow.changed'){extensions.refreshWorkflows();return;}
    const e = normalizeToolEvent(event);
    if (!e.session_id) return;
    if (live) {
      revisions.set(e.session_id,(revisions.get(e.session_id) || 0)+1);
      if (e.type === 'tool.started') active.set(e.session_id, e.turn_id);
      if (['playback.clear','turn.completed','session.disconnected'].includes(e.type)) active.delete(e.session_id);
    }
    if (!e.type.startsWith('tool.') && !['playback.clear','error','turn.completed'].includes(e.type)) return;
    events.set(JSON.stringify([e.session_id,e.turn_id,e.type,e.timestamp,e.seq,e.tool]), e);
    while (events.size > 3000) events.delete(events.keys().next().value);
  }
  async function loadCalls(sid, messages) {
    const tids = [...new Set(messages.map(m => m.turn_id).filter(Boolean))];
    if (!tids.length) return;
    // Message paging can show many turns; keep each server request bounded.
    for (let start=0; start<tids.length; start+=100) {
      const query = new URLSearchParams();
      tids.slice(start,start+100).forEach(tid => query.append('turn_id',tid));
      const revision = revisions.get(sid);
      const result = await api('sessions/'+encodeURIComponent(sid)+'/tool-calls?'+query);
      result.events.forEach(e => track(e,false));
      if (revisions.get(sid) === revision) active.set(sid,result.active_turn_id);
    }
  }
  function renderCalls(sid, tid) {
    return toolCallsForTurn([...events.values()],sid,tid,active.get(sid)).map(call => {
      const key = sid+'/'+tid+'/'+call.name;
      const box = node('details','tool-call '+call.phase);
      box.dataset.phase = call.phase;
      box.dataset.started = call.started;
      box.open = expanded.has(key);
      box.addEventListener('toggle', () => box.open ? expanded.add(key) : expanded.delete(key));
      const summary = node('summary');
      const elapsed = node('span','tool-call-time',call.elapsed === undefined ? '' : duration(call.elapsed));
      if (call.phase === 'running') {
        elapsed.dataset.elapsed = '';
        elapsed.textContent = duration(Math.max(0,Date.now()-call.started*1000));
      }
      summary.append(node('span','tool-call-mark','MCP'),node('strong','',names[call.name] || call.name),
        node('span','tool-call-state',phases[call.phase]),elapsed);
      const body = node('div','tool-call-body');
      body.append(node('p','hint',call.name+' · '+(call.planner === 'rex' ? '本地 Rex 决策' :
        call.planner === 'llm' ? '对话模型决策' : call.planner === 'dynamic_llm' ? '动态工具目录 · 对话模型决策' :
        call.planner === 'file_rules' ? '明确文件整理指令解析' : call.planner === 'music_rules' ? '明确音乐指令解析' : '决策方式未记录')));
      body.append(node('p','tool-call-caption','调用参数'),
        node('pre','',call.arguments === undefined ? '历史记录未保存参数' : JSON.stringify(call.arguments,null,2)));
      if (call.phase === 'failed') body.append(node('p','tool-call-error',
        (errors[call.code] || '本次调用未成功，请查看事件日志或稍后重试。')+(call.code ? '（'+call.code+'）' : '')));
      if (call.phase === 'cancelled') body.append(node('p','hint','本轮已被打断，工具结果不会继续播报。'));
      if (call.phase === 'unknown') body.append(node('p','hint','未收到完整结束记录，不能确认执行结果。'));
      if (call.phase === 'completed') body.append(node('p','hint','工具查询完成，结果将由助手在回复中说明。'));
      box.append(summary,body);
      box.setAttribute('aria-label',(names[call.name] || call.name)+'，'+phases[call.phase]);
      return box;
    });
  }
  function renderCatalogue() {
    const target = $('mcp-tool-list');
    const opened=new Set([...target.querySelectorAll('.mcp-tool-details[open]')].map(detail=>detail.parentElement.dataset.tool));
    extensions.parkSettings();
    target.replaceChildren();
    $('mcp-summary').textContent = (catalogue.tools.length+1)+' 项能力 · 点击具体工具配置';
    $('mcp-planner').textContent = catalogue.planner === 'rex' ? '本地 Rex · 决策与参数抽取' : '对话模型 · JSON 规划';
    if(catalogue.tools.some(t=>t.server_id!=='common-tools' && t.enabled))$('mcp-planner').textContent+=' · 扩展由对话模型规划';
    extensions.render(catalogue);
    if (!catalogue.tools.length) target.append(node('p','hint','还没有已注册的工具。'));
    for (const tool of catalogue.tools) {
      const card = node('article','panel mcp-tool');card.dataset.tool=tool.name;
      const heading = node('div','section-heading'), title = node('div');
      title.append(node('h2','',names[tool.name] || tool.name),node('small','mono',tool.name));
      const label = node('label','check');
      const toggle = node('input');
      toggle.type = 'checkbox'; toggle.checked = tool.enabled;
      toggle.disabled = saving;
      toggle.setAttribute('aria-label',(names[tool.name] || tool.name)+'启用');
      label.append(toggle,node('span','',tool.enabled ? '已启用' : '已停用'));
      toggle.onchange = async () => {
        if (saving) return;
        saving = true;
        $('mcp-tool-list').querySelectorAll('.section-heading input').forEach(input => input.disabled = true);
        try {
          if(tool.server_id!=='common-tools') {
            await api('mcp/servers/'+tool.server_id+'/'+(toggle.checked?'enable':'disable'),{
              method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({trust:false})});
          } else {
            const {config} = await api('config');config.tools[tool.name] = toggle.checked;
            const saved = await api('config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(config)});
            onConfig(saved.config);
          }
          await refreshCatalogue();
          notice('工具设置已保存，重新连接客户端后生效。');
        } catch (e) { toggle.checked = tool.enabled; notice(e.message,true); }
        finally {
          saving = false;
          $('mcp-tool-list').querySelectorAll('.section-heading input').forEach(input => input.disabled = false);
        }
      };
      if(tool.server_id==='common-tools')heading.append(title,label);
      else heading.append(title,node('span','tag',tool.enabled?'外部服务 · 已启用':'外部服务 · 已停用'));
      card.append(heading,node('p','',descriptions[tool.name] || tool.description),node('p','hint',tool.server_id!=='common-tools' ?
        '用户 MCP · 通过下方服务开关控制' : tool.name==='calendar' ? '本地日程 · 按配置时区提醒' : tool.name==='file_organizer' ? '本机文件元数据 · 预览后页面确认' : tool.name==='music' ? '播放控制 · 后端电脑音频输出' :
        tool.name === 'system_info' ? '本机读取 · 只读' : '需要联网 · 只读'));
      const schema = node('details','mcp-schema');
      schema.append(node('summary','','查看调用参数'));
      const list = node('dl','mcp-parameters');
      for (const [key,value] of Object.entries(tool.inputSchema.properties)) {
        const term = node('dt','',(fields[key] || key)+' · '+key);
        const bits = [tool.inputSchema.required?.includes(key) ? '必填' : '可选',value.type];
        if (value.enum) bits.push(value.enum.join(' / '));
        if (value.items?.enum) bits.push(value.items.enum.join(' / '));
        if (value.minimum !== undefined) bits.push(value.minimum+'–'+value.maximum);
        if (value.description) bits.push(value.description);
        list.append(term,node('dd','',bits.join(' · ')));
      }
      schema.append(list);
      const details=node('details','mcp-tool-details');details.open=opened.has(tool.name);details.append(node('summary','','配置与测试'));
      const settings=extensions.toolSettings(tool.name);if(settings)details.append(settings);
      details.append(node('p','mcp-example','试着说：“'+(examples[tool.name] || tool.name)+'”'),schema,extensions.toolTest(tool));
      card.append(details);
      target.append(card);
    }
    target.append(extensions.emailCard);
  }
  async function refreshCatalogue() {
    $('mcp-refresh').disabled = true;
    $('mcp-load-status').textContent = '正在读取工具列表…';
    try {
      catalogue = await api('mcp');
      renderCatalogue();
      $('mcp-load-status').textContent = '';
    } catch (e) {
      $('mcp-load-status').textContent = '工具列表加载失败：'+e.message+'。请点击刷新重试。';
    } finally { $('mcp-refresh').disabled = false; }
  }
  $('mcp-refresh').onclick = refreshCatalogue;
  window.addEventListener('voicehub:view', e => { if (e.detail === 'mcp') void refreshCatalogue(); });
  setInterval(() => {
    document.querySelectorAll('.tool-call[data-phase="running"]').forEach(el => {
      el.querySelector('[data-elapsed]').textContent = duration(Math.max(0,Date.now()-Number(el.dataset.started)*1000));
    });
  },1000);
  return {track, loadCalls, renderCalls, refreshCatalogue, unconfirm:() => active.clear()};
}
