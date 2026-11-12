"""Build an isolated v33-based API candidate. Never deploy or access a network."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MANIFEST=ROOT/'patches/notification-automation-api-base.json'


def canonical(text):return text.replace('\r\n','\n').rstrip('\n')+'\n'
def sha(text):return hashlib.sha256(canonical(text).encode()).hexdigest()
def once(text,old,new):
    if text.count(old)!=1:raise ValueError('reviewed_api_anchor_changed')
    return text.replace(old,new,1)


def build(baseline,output):
    manifest=json.loads(MANIFEST.read_text('utf-8'))
    files={name:canonical((baseline/name).read_text('utf-8')) for name in manifest['files']}
    if {name:sha(text) for name,text in files.items()}!=manifest['files']:
        raise ValueError('reviewed_api_baseline_changed')
    files['guarded_wechat_publish.ts']=once(files['guarded_wechat_publish.ts'],'function safeText(','export function safeText(')
    files['guarded_wechat_publish.ts']=once(files['guarded_wechat_publish.ts'],
        '各位同学|全体同学|所有同学|同学们|老师们','各位同学|全体同学|全班同学|每位同学|所有同学|同学们|老师们')
    t=files['receipt_fence_experiment.ts']
    edits=[
      ('if (existing.length!==4) return false;','if (existing.length!==records.length) return false;'),
      ('export function experimentalPublisher(client:any) {','export function experimentalPublisher(client:any,validate=validateWechatBundle) {'),
      ('const body=await validateWechatBundle(input);','const body=await validate(input);'),
      ('if (inserted.length!==4) conflict();','if (inserted.length!==body.records.length) conflict();'),
      ('export function experimentalReconciler(client:any) {','export function experimentalReconciler(client:any,validate:any=validateReconciliation) {'),
      ('const value=validateReconciliation(input);','const value=await validate(input);'),
      ('if (priorFence || existing.length!==4) conflict();','if (priorFence || existing.length!==value.records.length) conflict();'),
      ('export function createReceiptFencePublisher(options:FactoryOptions) {','export function createReceiptFencePublisher(options:FactoryOptions,validate=validateWechatBundle) {'),
      ('await validateWechatBundle(input);','await validate(input);'),
      ('experimentalPublisher(pool)(workspace,processor,input)','experimentalPublisher(pool,validate)(workspace,processor,input)'),
      ('export function createReceiptFenceReconciler(options:FactoryOptions) {','export function createReceiptFenceReconciler(options:FactoryOptions,validate:any=validateReconciliation) {'),
      ('validateReconciliation(input);','await validate(input);'),
      ('experimentalReconciler(pool)(workspace,processor,input)','experimentalReconciler(pool,validate)(workspace,processor,input)'),
    ]
    for old,new in edits:t=once(t,old,new)
    files['receipt_fence_experiment.ts']=t
    handler=files['handler.ts']
    anchor='notePublish?:(workspace:string,processor:string,id:string,body:any)=>Promise<any>'
    handler=once(handler,anchor,anchor+';guardedWechatActions?:(workspace:string,processor:string,body:any)=>Promise<any>;guardedWechatActionsReconcile?:(workspace:string,processor:string,body:any)=>Promise<any>;guardedWechatActionsStatus?:(workspace:string,processor:string)=>Promise<any>')
    routes='''    if(path==='/worker/wechat-actions-status'&&req.method==='GET') {
      if(!options.guardedWechatActionsStatus)throw new AppFault(503,'automation_disabled','自动事项同步尚未启用。');
      const approved=await options.guardedWechatActionsStatus(workspace,principal.device.id);
      const rows=await store.db('eric_app_workers?workspace_id=eq.'+workspace+'&select=publisher_epoch,source_generation&limit=2');
      if(rows.length!==1||!UUID.test(rows[0].publisher_epoch)||!Number.isSafeInteger(rows[0].source_generation))throw new AppFault(503,'publisher_unavailable','发布会话暂时不可用。');
      return json({...approved,workspace_id:workspace,processor_id:principal.device.id,epoch:rows[0].publisher_epoch,generation:rows[0].source_generation});
    }
    if(path==='/worker/wechat-actions'&&req.method==='POST') {
      if(!options.guardedWechatActions)throw new AppFault(503,'automation_disabled','自动事项同步尚未启用。');
      return json(await options.guardedWechatActions(workspace,principal.device.id,await boundedJson(req,512_000)));
    }
    if(path==='/worker/wechat-actions-reconcile'&&req.method==='POST') {
      if(!options.guardedWechatActionsReconcile)throw new AppFault(503,'automation_disabled','自动事项同步尚未启用。');
      return json(await options.guardedWechatActionsReconcile(workspace,principal.device.id,await boundedJson(req,32_000)));
    }
'''
    handler=once(handler,"    if(path==='/worker/wechat-publish'",routes+"    if(path==='/worker/wechat-publish'")
    files['handler.ts']=handler
    files['views.ts']=once(files['views.ts'],"if (!item.confirmed_by_user || item.uncertain", "if (!(item.confirmed_by_user || item.verified_automatically===true) || item.uncertain")
    index=files['index.ts']
    index=once(index,"const named=",'''let automatic:any;
async function automaticEndpoints(){
  automatic ||= (await import('./guarded_wechat_actions.ts')).createAutomaticTaskEndpoints({
    databaseUrl:Deno.env.get('SUPABASE_DB_URL')||'',scope:Deno.env.get('ERIC_NOTIFICATION_AUTOMATION_SCOPE')||''});
  return automatic;
}
const named=''')
    index=once(index,'  origins:[', '''  guardedWechatActions:async(w,p,b)=>(await automaticEndpoints()).publish(w,p,b),
  guardedWechatActionsReconcile:async(w,p,b)=>(await automaticEndpoints()).reconcile(w,p,b),
  guardedWechatActionsStatus:async(w,p)=>(await automaticEndpoints()).status(w,p),
  origins:[''')
    files['index.ts']=index
    files['guarded_wechat_actions.ts']=canonical((ROOT/'supabase/functions/eric-app-api/guarded_wechat_actions.ts').read_text('utf-8'))
    if output.exists():raise ValueError('new_isolated_output_required')
    output.mkdir(parents=True)
    for name,text in files.items():(output/name).write_text(text,'utf-8',newline='\n')
    proof={'baseline_version':manifest['api_version'],'baseline_bundle_hash':manifest['bundle_hash'],
           'files':{name:sha(text) for name,text in files.items()},'deployment_performed':False,
           'default_enabled':False,'schema_changes':False,'hash_normalization':'UTF-8/LF; trailing blank lines removed'}
    (output/'candidate-manifest.json').write_text(json.dumps(proof,indent=2),'utf-8')
    return proof


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();result=build(a.baseline,a.output)
    print(json.dumps({'files':len(result['files']),'baseline_version':result['baseline_version'],'deployed':False}))
