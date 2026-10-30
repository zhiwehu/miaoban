import test from 'node:test';
import assert from 'node:assert/strict';
import {setupResponse} from '../voicehub/static/echoear-response.mjs';
test('non-JSON authorization/proxy responses produce a readable connection error',async()=>{
 await assert.rejects(()=>setupResponse(new Response('Cross-origin request denied',{status:403})),/页面地址校验失败/);
 await assert.rejects(()=>setupResponse(new Response('<html>unauthorized</html>',{status:401})),/管理员/);
 await assert.rejects(()=>setupResponse(new Response('<html>proxy error</html>',{status:502})),/异常响应/);
});
test('valid setup JSON is parsed, malformed success is rejected',async()=>{
 assert.deepEqual(await setupResponse(new Response('{"host":"192.168.1.1"}')),{host:'192.168.1.1'});
 await assert.rejects(()=>setupResponse(new Response('null')),/数据格式/);
});
