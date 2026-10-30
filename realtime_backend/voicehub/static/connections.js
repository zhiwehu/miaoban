const $ = id => document.getElementById(id);
const labels = {running:'运行中',connecting:'连接 Broker 中',stopped:'未启动',failed:'启动失败',
  websocket_v1:'WebSocket v1',xiaozhi_websocket:'小智 WebSocket',mqtt_udp:'MQTT + UDP',
  listening:'聆听中',standby:'待机',thinking:'识别 / 思考中',speaking:'播报中'};
const errors = {mqtt_not_configured:'尚未初始化 MQTT 凭据。请先按部署说明配置 Broker 和设备。',
  mqtt_invalid_config:'MQTT 私有配置无效，请检查文件权限和配置内容。',mqtt_must_stop:'请先停止 MQTT 网关，再保存配置。',
  udp_address_unavailable:'UDP 监听地址不在当前电脑上，请选择下方列出的局域网地址。',
  udp_port_in_use:'UDP 端口被占用，请更换端口或检查占用程序。',broker_unavailable:'无法连接 Broker，正在自动重试。请核对地址、端口、服务和凭据。',
  mqtt_dependency_missing:'缺少 MQTT 运行依赖，请安装 requirements-mqtt.txt。',device_must_stop:'请先停止设备监听，再保存配置。',
  device_address_unavailable:'设备监听地址或端口不可用，请核对当前网卡及端口。',invalid_device_address:'请选择有效的局域网 IPv4 地址。'};
let data, selected='web', loading=false, busy=false;
const dirty = {mqtt:false,websocket:false};
function el(tag, text, cls) { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; }
function info(text, bad=false) { $('connections-notice').textContent=text; $('connections-notice').classList.toggle('error',bad); }
async function request(path='', options={}) {
  const response=await fetch('/api/connections'+path,options);
  if(response.status===401)throw new Error('请先使用管理员令牌登录。');
  const value=await response.json();
  if(!response.ok) throw new Error(errors[value.error?.code] || value.error?.message || '请求失败，请检查管理员登录状态。');
  return value;
}
function link(text, href) { const a=el('a',text,'secondary connection-link'); a.href=href; if(href.startsWith('https:')) {a.target='_blank';a.rel='noopener noreferrer';}return a; }
function drawCatalog() {
  const target=$('client-catalog'); target.replaceChildren();
  for(const client of data.clients) {
    const card=el('article',undefined,'panel client-choice');
    card.append(el('span',client.verified,'tag'),el('h2',client.name),el('p',client.id==='web'?'适合先体验聊天，也支持耳机麦克风。':'适合已经拥有这款猫头设备的用户。','hint'));
    const choose=el('button',selected===client.id?'已选择':'选择此客户端',selected===client.id?'primary':'secondary');
    choose.type='button'; choose.setAttribute('aria-pressed',String(selected===client.id));
    choose.onclick=()=>{selected=client.id;drawCatalog();drawGuide();};
    card.append(choose);target.append(card);
  }
}
function drawGuide() {
  const client=data.clients.find(c=>c.id===selected) || data.clients[0];
  $('echoear-installer').hidden=client.id!=='echoear-s3';
  window.dispatchEvent(new CustomEvent('voicehub:firmware',{detail:client.firmware}));
  $('client-guide-title').textContent='2. '+(client.id==='web'?'打开网页，开始使用':'准备设备并连接');
  $('client-guide-status').textContent=client.id==='web'?'无需安装':'请先确认设备型号';
  $('client-guide-hardware').textContent=client.hardware;
  const steps=$('client-guide-steps');steps.replaceChildren();
  for(const text of client.steps)steps.append(el('li',text));
  $('client-install-note').textContent=client.installation;
  const actions=$('client-install-actions');actions.replaceChildren();
  if(client.id==='web') {const start=link('打开网页，开始聊天','#lab');start.classList.add('primary');actions.append(start);}
  else {
    if(client.firmware) {
      if(client.firmware.install?.validated!==false)actions.append(link('下载应用固件 '+client.firmware.version,client.firmware.download));
      else actions.append(el('p','新版应用已构建，正在等待实机验收。通过验收后，这里会开放安装。','hint'));
      const checksum=el('details');checksum.append(el('summary','查看固件校验信息'),el('small','SHA256 '+client.firmware.sha256,'mono'));actions.append(checksum);
    } else actions.append(el('p','这台服务器还没有提供固件安装包，请联系管理员准备。设备已经安装适配固件的，可以继续配置连接。','hint'));
    const install=el('details',undefined,'connection-install-details');install.append(el('summary','安装说明与其他连接方式'));
    if(client.source_url) install.append(link('查看固件源码',client.source_url));
    if(client.build_url) install.append(link('查看安装与构建说明',client.build_url));
    if(client.firmware?.install?.validated===false)install.append(link('下载候选固件（管理员测试）',client.firmware.download));
    actions.append(install);
    if(data.websocket.device_ready) {
      install.append(el('p','设备连接地址：ws://'+data.websocket.device_host+':'+data.websocket.device_port+'/xiaozhi/v1/','hint'));
      actions.append(link('下载推荐的设备连接设置','/api/clients/echoear-s3/connection?protocol=xiaozhi_websocket'));
      for(const [device, enabled] of Object.entries(data.mqtt.config?.devices || {})) {
        if(enabled && data.mqtt.state === 'running')install.append(link('下载 '+device+' 的 MQTT 配网配置','/api/clients/echoear-s3/connection?protocol=mqtt_udp&device='+encodeURIComponent(device)));
      }
      actions.append(el('p','下载的连接配置包含设备访问凭据，请仅交给对应设备使用。按固件说明填入地址与令牌，下载不会自动改写设备。','hint'));
    } else {
      actions.append(el('p','下方 USB 向导会自动准备设备连接服务，无需手动填写地址。','hint'));
      const setup=el('button','打开高级连接设置','secondary');setup.type='button';setup.onclick=()=>{$('connection-advanced').open=true;$('connection-advanced').scrollIntoView({behavior:'smooth',block:'start'});};actions.append(setup);
    }
  }
}
function sessionButtons(item) {
  const row=el('div',undefined,'button-row');
  for(const [target,title] of [['conversations','对话记录'],['logs','会话日志']]) {
    const b=el('button',title,'secondary');b.type='button';
    b.onclick=()=>window.dispatchEvent(new CustomEvent('voicehub:open-session',{detail:{sid:item.id,target}}));
    row.append(b);
  }
  return row;
}
function drawDevices() {
  $('connections-online-count').textContent=data.devices.length+' 在线';
  const target=$('connections-devices');target.replaceChildren();
  for(const device of data.devices) {
    const row=el('div',undefined,'connection-device');
    row.append(el('strong',device.device_id),el('span',labels[device.protocol]||device.protocol,'tag'),
      el('span',labels[device.state]||device.state,'tag'),el('small',device.id.slice(0,12),'mono'));
    if(device.audio)row.append(el('p',`音频包：接收 ${device.audio.received} · 发送 ${device.audio.sent} · 缺包 ${device.audio.lost} · 队列丢包 ${device.audio.dropped}`,'hint'));
    row.append(sessionButtons(device));target.append(row);
  }
  if(!data.devices.length)target.append(el('p','还没有设备连上来。完成上面的步骤后，这里会显示设备名称；连接成功后就可以开始对话。','hint'));
  const recent=$('connections-recent');recent.replaceChildren();
  for(const item of data.recent) {
    const row=el('div',undefined,'connection-device');
    row.append(el('strong',item.device_id),el('small',new Date(item.updated*1000).toLocaleString('zh-CN'),'hint'),sessionButtons(item));
    recent.append(row);
  }
  if(!data.recent.length)recent.append(el('p','尚无历史会话。','hint'));
}
function drawSettings() {
  const ws=data.websocket, mqtt=data.mqtt;
  $('ws-device-state').textContent=ws.device_ready?'设备监听运行中':ws.device_error?'启动失败':'设备监听未启动';
  $('ws-console-address').textContent='当前网页入口：'+location.origin.replace(/^http/,'ws')+'/ws';
  $('ws-device-address').textContent=ws.device_error?(errors[ws.device_error]||ws.device_error):
    ws.device_ready?'硬件设备入口：ws://'+ws.device_host+':'+ws.device_port+'/xiaozhi/v1/':'选择本机局域网地址后启动设备专用监听。';
  if(!dirty.websocket) {$('ws-device-host').value=ws.device_host||'';$('ws-device-port').value=ws.device_port;}
  for(const n of $('ws-connection-form').elements)n.disabled=busy||ws.device_ready;
  $('mqtt-connection-state').textContent=mqtt.configured?(labels[mqtt.state]||mqtt.state):'尚未配置';
  $('mqtt-connection-warning').textContent=mqtt.error?(errors[mqtt.error]||mqtt.error):
    !mqtt.configured?'首次接入需初始化 Broker 账号、设备账号和 ACL，按部署说明生成私有配置。':
    mqtt.address_available===false?'配置的 UDP 地址不属于当前电脑。先停止网关，改为当前局域网地址后再启动。':
    '控制消息走 MQTT，音频走 UDP；停止网关会断开 MQTT 设备会话。';
  if(!dirty.mqtt && mqtt.config) {
    for(const [key,id] of Object.entries({broker_host:'mqtt-broker-host',broker_port:'mqtt-broker-port',udp_host:'mqtt-udp-host',udp_port:'mqtt-udp-port'}))$(id).value=mqtt.config[key];
    const devices=$('mqtt-configured-devices');devices.replaceChildren();
    for(const [id,enabled] of Object.entries(mqtt.config.devices)) {
      const label=el('label',undefined,'check-row'),input=el('input');input.type='checkbox';input.checked=enabled;input.dataset.device=id;
      label.append(input,el('span',id));devices.append(label);
    }
  }
  for(const n of $('mqtt-connection-form').elements)n.disabled=busy||!mqtt.configured||['running','connecting'].includes(mqtt.state);
  for(const [id,ready,configured] of [['ws-connection-actions',ws.device_ready,true],['mqtt-connection-actions',['running','connecting'].includes(mqtt.state),mqtt.configured]]) {
    for(const b of $(id).querySelectorAll('button'))b.disabled=busy||!configured||(b.dataset.action==='start'?ready:!ready);
  }
  const addresses=$('local-addresses');addresses.replaceChildren();
  for(const item of data.interfaces) {const o=el('option');o.value=item.address;o.label=item.interface;addresses.append(o);}
  $('local-address-hint').textContent='当前本机 IPv4：'+(data.interfaces.map(i=>i.interface+' '+i.address).join(' · ')||'未检测到局域网地址');
}
async function refresh() {
  if(loading||busy)return;loading=true;
  try {
    const first=!data;data=await request();
    if(first)drawCatalog();drawGuide();drawSettings();drawDevices();
    info('按下面三步完成接入，连接结果会自动更新。');
  } catch(e) {info(e.message,true);} finally {loading=false;}
}
async function mutate(path,value) {
  if(busy||!data)return;busy=true;drawSettings();
  try {
    data=await request(path,{method:value?'PUT':'POST',...(value?{headers:{'Content-Type':'application/json'},body:JSON.stringify(value)}:{})});
    if(value)dirty[path.includes('websocket')?'websocket':'mqtt']=false;
    drawSettings();drawDevices();drawGuide();info(value?'配置已保存。':'操作完成，连接状态会自动更新。');
  } catch(e) {info(e.message,true);} finally {busy=false;drawSettings();}
}
$('ws-connection-form').oninput=()=>{dirty.websocket=true;};
$('mqtt-connection-form').oninput=()=>{dirty.mqtt=true;};
$('ws-connection-form').onsubmit=e=>{e.preventDefault();mutate('/websocket',{host:$('ws-device-host').value,port:Number($('ws-device-port').value)});};
$('mqtt-connection-form').onsubmit=e=>{
  e.preventDefault();const devices={};for(const n of $('mqtt-configured-devices').querySelectorAll('input'))devices[n.dataset.device]=n.checked;
  mutate('/mqtt',{broker_host:$('mqtt-broker-host').value,broker_port:Number($('mqtt-broker-port').value),udp_host:$('mqtt-udp-host').value,udp_port:Number($('mqtt-udp-port').value),devices});
};
for(const [id,transport] of [['ws-connection-actions','websocket'],['mqtt-connection-actions','mqtt']])for(const b of $(id).querySelectorAll('button'))b.onclick=()=>mutate('/'+transport+'/'+b.dataset.action);
$('connections-refresh').onclick=refresh;
window.addEventListener('voicehub:view',event=>{if(event.detail==='connections')refresh();});
window.addEventListener('voicehub:connection-prepared',refresh);
window.addEventListener('hashchange',()=>{if(location.hash==='#connections')refresh();});
setInterval(()=>{if(location.hash==='#connections'&&!document.hidden)refresh();},3000);
if(location.hash==='#connections')refresh();
