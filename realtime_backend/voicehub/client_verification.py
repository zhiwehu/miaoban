"""Per-turn evidence; generated audio is never labelled physically heard automatically."""
def evidence(events):
    turns = {}
    for item in events:
        payload = item.get('payload',{})
        turn = item.get('turn_id') or payload.get('reported_turn_id')
        if not turn: continue
        state = turns.setdefault(turn,dict(recognized=False,replied=False,ready=None,stopped=None))
        if item['type']=='transcript.final':state['recognized']=True
        if item['type']=='reply.final':state['replied']=True
        if item['type']=='device.playback_state' and payload.get('output_enabled') is True:
            count=payload.get('played_audio_packets')
            if type(count) is int and payload.get('state') in ('ready','stopped'):
                state[payload['state']]=count
    selected = next((v for v in turns.values() if v['recognized'] and v['replied']),None)
    if selected is None:selected=next(iter(turns.values()),{})
    ready,stopped=selected.get('ready'),selected.get('stopped')
    return dict(recognized=selected.get('recognized',False),replied=selected.get('replied',False),
                playback_reported=ready is not None and stopped is not None and 0<(stopped-ready)&0xffffffff<=2000)
