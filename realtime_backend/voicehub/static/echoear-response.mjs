// Do not interpret a proxy/authentication error page as a JSON tool result.
export async function setupResponse(response){
 const text=await response.text();let value;
 try{value=JSON.parse(text);}catch{
  throw new Error(response.status===403?'页面地址校验失败，请刷新并使用同一 VoiceHub 地址重试。':response.status===401?'请先使用管理员账号登录。':'设备连接服务返回了异常响应，请刷新页面后重试。');
 }
 if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('设备连接服务返回的数据格式无效。');
 return value;
}
