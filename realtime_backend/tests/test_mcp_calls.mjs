import {test} from 'node:test';
import assert from 'node:assert/strict';
import {toolCallsForTurn} from '../voicehub/static/mcp-calls.mjs';
const event = (type,timestamp,extra={}) => ({type,timestamp,session_id:'s',turn_id:'t',tool:'news',...extra});

test('history and live delivery order produce one call with arguments and duration', () => {
  const start = event('tool.started',1,{arguments:{limit:2},planner:'rex'});
  const done = event('tool.completed',2,{elapsed_ms:950});
  const calls = toolCallsForTurn([done,start,start],'s','t',null);
  assert.equal(calls.length,1);
  assert.equal(calls[0].phase,'completed');
  assert.deepEqual(calls[0].arguments,{limit:2});
  assert.equal(calls[0].elapsed,950);
});
test('interruption cancels pending calls but preserves an already completed query', () => {
  const start = event('tool.started',1);
  const cancel = event('playback.clear',3,{turn_id:null,interrupted_turn_id:'t',reason:'client'});
  assert.equal(toolCallsForTurn([start,cancel],'s','t',null)[0].phase,'cancelled');
  assert.equal(toolCallsForTurn([start,event('tool.completed',2),cancel],'s','t',null)[0].phase,'completed');
});
test('failed tools keep error codes; abandoned history is never shown as still running', () => {
  const start = event('tool.started',1);
  assert.equal(toolCallsForTurn([start],'s','t','t')[0].phase,'running');
  assert.equal(toolCallsForTurn([start],'s','t',null)[0].phase,'unknown');
  const failed = event('tool.failed',2,{code:'upstream_timeout',elapsed_ms:1000});
  assert.equal(toolCallsForTurn([start,failed],'s','t',null)[0].code,'upstream_timeout');
  assert.deepEqual(toolCallsForTurn([start,failed],'other','t',null),[]);
});

test('background calls remain running across interruption and foreground completion',()=>{
 const events=[{type:'tool.started',session_id:'s',turn_id:'t',tool:'slow',background:true,timestamp:1},
  {type:'playback.clear',session_id:'s',interrupted_turn_id:'t',timestamp:2},
  {type:'turn.completed',session_id:'s',turn_id:'t',timestamp:3}];
 const [call]=toolCallsForTurn(events,'s','t','new');assert.equal(call.phase,'running');
 events.push({type:'tool.completed',session_id:'s',turn_id:'t',tool:'slow',timestamp:4,elapsed_ms:3000});
 assert.equal(toolCallsForTurn(events,'s','t','new')[0].phase,'completed');
});
