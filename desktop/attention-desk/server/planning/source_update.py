"""Read-only comparison of a new proposal with earlier confirmed facts."""
def comparison(connection,raw_item_id,snapshot_id,extraction):
    events = []
    for row in connection.execute("SELECT id,title,status,version,raw_snapshot_id FROM event WHERE raw_item_id=? AND raw_snapshot_id<>? ORDER BY created_at,id",(raw_item_id,snapshot_id)):
        event = dict(row)
        event['deadlines'] = [dict(value) for value in connection.execute('SELECT * FROM deadline WHERE event_id=? AND confirmed_by_user=1 ORDER BY value,id',(event['id'],))]
        if event['deadlines']:
            events.append(event)
    output = (extraction or {}).get('parsed_output') or {}
    proposals = []
    for candidate in output.get('events',[]):
        for deadline in candidate.get('deadlines',[]):
            conflicts = []
            for event in events:
                for old in event['deadlines']:
                    if old['value']!=deadline.get('normalized_date') or old['precision']!=deadline['precision']:
                        conflicts.append({'event_id':event['id'],'deadline_id':old['id'],'old_value':old['value'],
                                          'match':'role' if old['label']==deadline['role'] else 'manual'})
            proposals.append({**deadline,'event_title':candidate['title'],'conflicts':conflicts})
    return {'events':events,'proposals':proposals,'requires_confirmation':bool(events)}
