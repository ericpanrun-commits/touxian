"""Read official provenance alongside the shared immutable snapshot."""
import json


def model_source_context(snapshot):
    web = snapshot.get('web')
    if not web:
        return None
    return {'page_title': web['page_title'], 'published_at': web['published_at'],
            'attachments': web['attachments'], 'body_status': web['body_status'], 'unparsed_image_count': len(web['images']),
            'parsed_files': [{k:a.get(k) for k in ('id','filename','sha256','version','extractor_version')} for a in snapshot.get('attachments',[]) if a['parse_status']=='parsed']}


def snapshot_with_metadata(connection, snapshot):
    result = dict(snapshot)
    document = connection.execute('SELECT * FROM snapshot_document WHERE raw_snapshot_id=?', (result['id'],)).fetchone()
    web = connection.execute('SELECT * FROM raw_snapshot_web WHERE raw_snapshot_id=?', (document['body_snapshot_id'] if document else result['id'],)).fetchone()
    if web:
        result['web'] = dict(web)
        result['web'].pop('body_html')  # Stored for provenance, never sent to an HTML renderer.
        result['web']['attachments'] = json.loads(result['web'].pop('attachments_json'))
        result['web']['images'] = json.loads(result['web'].pop('images_json'))
    from .documents.blocks import read_blocks, select_model_blocks
    result['blocks'] = read_blocks(connection,result['id'])
    result['attachments'] = []
    for row in connection.execute('SELECT a.* FROM attachment a JOIN snapshot_attachment sa ON sa.attachment_id=a.id WHERE sa.raw_snapshot_id=? ORDER BY a.created_at,a.id', (result['id'],)):
        value = dict(row)
        value.pop('local_path',None)
        value.pop('extracted_text',None)
        manifest = json.loads(value.pop('parse_manifest_json') or 'null')
        value['parse_manifest'] = {'metadata':manifest.get('metadata',{})} if manifest else None
        result['attachments'].append(value)
    result['document_warnings'] = json.loads(document['warnings_json']) if document else []
    if result['blocks']:
        result['input_budget'] = {k:v for k,v in select_model_blocks(result['blocks']).items() if k!='blocks'}
    jobs = [dict(row) for row in connection.execute('SELECT * FROM attachment_job WHERE source_snapshot_id=? ORDER BY created_at DESC LIMIT 5',(result['id'],))]
    for job in jobs:
        job['attachment_ids'] = json.loads(job.pop('attachment_ids_json'))
        job['error'] = json.loads(job.pop('error_json') or 'null')
        if isinstance(job['error'],dict): job['error']=[job['error']]
    result['attachment_jobs'] = jobs
    if document:
        result['document'] = {k:document[k] for k in ('body_snapshot_id','version','manifest_hash')}
    newer = connection.execute('SELECT raw_snapshot_id FROM snapshot_document WHERE body_snapshot_id=? ORDER BY version DESC LIMIT 1',(document['body_snapshot_id'] if document else result['id'],)).fetchone()
    if newer and newer[0]!=result['id']: result['latest_document_snapshot_id']=newer[0]
    return result
