import { AppFault, UUID } from './store.ts';
import { LOCK_SQL, PROCESSOR_SQL, WORKER_SQL, clientFor } from './guarded_wechat_publish.ts';

// Candidate only. Never replace an existing publisher merely because a lease
// looks old; an unknown owner must be resolved separately before rollout.
export const BEGIN_SQL='SELECT public.eric_app_begin_publish($1::uuid,$2::uuid)';
export const TOUCH_WORKER_SQL='UPDATE public.eric_app_workers SET last_seen_at=now() WHERE workspace_id=$1::uuid';
export const TOUCH_PROCESSOR_SQL='UPDATE public.eric_app_processors SET last_seen_at=now() WHERE workspace_id=$1::uuid AND id=$2::uuid AND revoked_at IS NULL';
const isUuid=(value:any)=>typeof value==='string' && UUID.test(value);

export function createGuardedWechatSession(options:{databaseUrl:string;clientFactory?:(url:string)=>Promise<any>}) {
  let client:Promise<any>|undefined;
  return async (workspace:string,processor:string,body:any):Promise<any> => {
    if (!isUuid(workspace) || !isUuid(processor) || !body || Array.isArray(body) ||
        typeof body!=='object' || Object.keys(body).length!==1 || !isUuid(body.epoch))
      throw new AppFault(400,'invalid_wechat_session','同步身份无效，内容仍保留在本机。');
    if (!options.databaseUrl)
      throw new AppFault(503,'scoped_publish_unavailable','同步保护暂时不可用，内容仍保留在本机。');
    try {
      client ||= (options.clientFactory||clientFor)(options.databaseUrl);
      const sql=await client;
      return await sql.begin(async(tx:any)=>{
        await tx.unsafe("SET LOCAL statement_timeout='5s'",[]);
        await tx.unsafe("SET LOCAL lock_timeout='2s'",[]);
        await tx.unsafe(LOCK_SQL,[workspace]);
        if ((await tx.unsafe(PROCESSOR_SQL,[workspace,processor])).length!==1)
          throw new AppFault(401,'worker_revoked','同步凭据已失效，内容仍保留在本机。');
        const workers=await tx.unsafe(WORKER_SQL,[workspace]);
        if (workers.length>1) throw new Error('invalid_worker_count');
        let generation=0;
        if (workers.length && workers[0].publisher_epoch!=null) {
          if (String(workers[0].publisher_epoch)!==body.epoch)
            throw new AppFault(409,'publisher_in_use','已有发布会话，已暂停等待核对。');
          generation=Number(workers[0].source_generation);
          if (!Number.isSafeInteger(generation) || generation<0) throw new Error('invalid_generation');
        } else {
          await tx.unsafe(BEGIN_SQL,[workspace,body.epoch]);
        }
        await tx.unsafe(TOUCH_WORKER_SQL,[workspace]);
        await tx.unsafe(TOUCH_PROCESSOR_SQL,[workspace,processor]);
        return {workspace_id:workspace,epoch:body.epoch,generation};
      });
    } catch(error) {
      if (error instanceof AppFault) throw error;
      client=undefined;
      throw new AppFault(503,'scoped_publish_unavailable','同步会话暂时无法确认，内容仍保留在本机。');
    }
  };
}

