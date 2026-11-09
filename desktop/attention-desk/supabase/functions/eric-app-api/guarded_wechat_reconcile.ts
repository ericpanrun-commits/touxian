import { AppFault, SHA, UUID, hash, stable } from './store.ts';
import { LOCK_SQL, PROCESSOR_SQL, WORKER_SQL, clientFor } from './guarded_wechat_publish.ts';

// Candidate only. Reconciliation sends hashes and identities, not notice text.
// A transaction fence prevents an old uncertain request from committing after
// the server has declared its target absent. It does not itself publish data.
const isUuid=(value:any)=>typeof value==='string' && UUID.test(value);
const isObject=(value:any,keys:string[])=>Boolean(value && typeof value==='object' && !Array.isArray(value)
  && stable(Object.keys(value).sort())===stable([...keys].sort()));
function invalid():never {throw new AppFault(400,'invalid_wechat_reconciliation','同步状态无法核对，内容仍保留在本机。');}
function conflict(code='wechat_target_conflict'):never {throw new AppFault(409,code,'同步目标状态不一致，已暂停等待核对。');}
export const RECONCILE_EXISTING_SQL=`SELECT r.kind,r.id,r.source_hash,r.payload,r.deleted
  FROM public.eric_app_records r JOIN jsonb_to_recordset(($2::text)::jsonb) AS p(kind text,id text,source_hash text)
    ON r.kind=p.kind AND r.id=p.id WHERE r.workspace_id=$1::uuid FOR UPDATE OF r`;
export const FENCE_SQL=`UPDATE public.eric_app_workers SET source_generation=$3::bigint
  WHERE workspace_id=$1::uuid AND publisher_epoch=$2::uuid RETURNING source_generation`;

export function validateReconciliation(value:any):any {
  if (!isObject(value,['epoch','generation','raw_id','snapshot_id','source_id','records']) ||
      !isUuid(value.epoch) || !isUuid(value.raw_id) || !isUuid(value.snapshot_id) ||
      !Number.isSafeInteger(value.generation) || value.generation<0 || value.generation>=Number.MAX_SAFE_INTEGER ||
      typeof value.source_id!=='string' || !/^wechat:[A-Za-z0-9_-]{1,80}$/.test(value.source_id) ||
      !Array.isArray(value.records) || value.records.length!==4) invalid();
  const expected:Record<string,string>={raw_item:value.raw_id,inbox_item:value.raw_id,inbox:value.raw_id,raw_review:value.snapshot_id};
  const seen=new Set<string>();
  for (const row of value.records) {
    if (!isObject(row,['kind','id','source_hash']) || !(row.kind in expected) || seen.has(row.kind) ||
        row.id!==expected[row.kind] || typeof row.source_hash!=='string' || !SHA.test(row.source_hash)) invalid();
    seen.add(row.kind);
  }
  return value;
}

export function createGuardedWechatReconciler(options:{databaseUrl:string;clientFactory?:(url:string)=>Promise<any>}) {
  let client:Promise<any>|undefined;
  return async (workspace:string,processor:string,input:any):Promise<any> => {
    if (!isUuid(workspace) || !isUuid(processor)) invalid();
    const value=validateReconciliation(input);
    if (!options.databaseUrl) throw new AppFault(503,'scoped_publish_unavailable','同步保护暂时不可用，内容仍保留在本机。');
    const proof=await hash(stable(value));
    try {
      client ||= (options.clientFactory||clientFor)(options.databaseUrl);
      const sql=await client,encoded=JSON.stringify(value.records);
      return await sql.begin(async(tx:any)=>{
        await tx.unsafe("SET LOCAL statement_timeout='5s'",[]);
        await tx.unsafe("SET LOCAL lock_timeout='2s'",[]);
        await tx.unsafe(LOCK_SQL,[workspace]);
        if ((await tx.unsafe(PROCESSOR_SQL,[workspace,processor])).length!==1)
          throw new AppFault(401,'worker_revoked','同步凭据已失效，内容仍保留在本机。');
        const workers=await tx.unsafe(WORKER_SQL,[workspace]);
        if (workers.length!==1 || String(workers[0].publisher_epoch)!==value.epoch) conflict('stale_publisher');
        const current=Number(workers[0].source_generation);
        if (!Number.isSafeInteger(current) || current<0 || current>=Number.MAX_SAFE_INTEGER) throw new Error('invalid_generation');
        // A result can refer to a prior cycle, or the next not-yet-committed
        // cycle, but cannot move the workspace counter by an arbitrary jump.
        if (value.generation>current+1) conflict('stale_publisher');
        const existing=await tx.unsafe(RECONCILE_EXISTING_SQL,[workspace,encoded]);
        if (existing.length) {
          if (existing.length!==4) conflict();
          const expected=new Map(value.records.map((r:any)=>[r.kind+'\u0000'+r.id,r.source_hash]));
          for (const row of existing) {
            const originalHash=expected.get(row.kind+'\u0000'+row.id);
            if (row.deleted!==false || originalHash!==row.source_hash ||
                await hash(stable(row.payload))!==originalHash ||
                (row.kind==='raw_item' && row.payload?.source_id!==value.source_id)) conflict();
          }
          return {state:'already_published',proof,generation:current};
        }
        const next=Math.max(current,value.generation+1);
        if (next!==current) {
          const updated=await tx.unsafe(FENCE_SQL,[workspace,value.epoch,next]);
          if (updated.length!==1 || Number(updated[0].source_generation)!==next) throw new Error('fence_failed');
        }
        return {state:'fenced_absent',proof,generation:next};
      });
    } catch(error) {
      if (error instanceof AppFault) throw error;
      client=undefined;
      throw new AppFault(503,'scoped_publish_unavailable','同步结果暂时无法确认，内容仍保留在本机。');
    }
  };
}

