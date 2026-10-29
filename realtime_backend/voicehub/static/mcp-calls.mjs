// Fold persisted and live events into one call per tool/turn, independent of delivery order.
export function normalizeToolEvent(event) {
  return event.payload
    ? {...event.payload, type:event.type, session_id:event.session_id, turn_id:event.turn_id,
       timestamp:event.payload.timestamp ?? event.created}
    : event;
}

export function toolCallsForTurn(events, sid, tid, activeTurn) {
  const calls = new Map();
  const relevant = events.map(normalizeToolEvent).filter(e => e.session_id === sid &&
    (e.turn_id === tid || e.interrupted_turn_id === tid));
  relevant.sort((a,b) => a.timestamp-b.timestamp || (a.seq || 0)-(b.seq || 0));
  for (const e of relevant) {
    if (e.type === 'tool.started') {
      calls.set(e.tool, {name:e.tool, phase:'running', arguments:e.arguments,
        planner:e.planner, started:e.timestamp, background:e.background === true});
    } else if (['tool.completed','tool.failed'].includes(e.type)) {
      const call = calls.get(e.tool) || {name:e.tool, started:e.timestamp};
      calls.set(e.tool, {...call, phase:e.type.split('.')[1], elapsed:e.elapsed_ms, code:e.code});
    } else if (['playback.clear','error','turn.completed'].includes(e.type)) {
      for (const call of calls.values()) {
        if (call.phase !== 'running' || call.background) continue;
        call.phase = e.type === 'error' || e.reason === 'error' ? 'failed' :
          e.type === 'turn.completed' ? 'unknown' : 'cancelled';
        call.code = e.code || '';
        call.elapsed = Math.max(0, Math.round((e.timestamp-call.started)*1000));
      }
    }
  }
  for (const call of calls.values()) {
    if (call.phase === 'running' && !call.background && activeTurn !== tid) call.phase = 'unknown';
  }
  return [...calls.values()];
}
