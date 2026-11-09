// Isolated experiment only. This module is not registered in the deployed API.
// It reuses the existing workspace lock and receipts table; it does not prove
// personal relevance, valid consent, or safety against uncoordinated writers.
import { AppFault, UUID, hash, stable } from './store.ts';
import { validateWechatBundle, LOCK_SQL, PROCESSOR_SQL, WORKER_SQL, EXISTING_SQL, INSERT_SQL } from './guarded_wechat_publish.ts';
import { validateReconciliation, RECONCILE_EXISTING_SQL } from './guarded_wechat_reconcile.ts';

const RECEIPT_READ='SELECT request_hash,result FROM public.eric_app_receipts WHERE workspace_id=$1::uuid AND request_id=$2::uuid FOR UPDATE';
const RECEIPT_INSERT=`INSERT INTO public.eric_app_receipts(workspace_id,request_id,request_hash,result)
  VALUES($1::uuid,$2::uuid,$3::text,($4::text)::jsonb) ON CONFLICT(workspace_id,request_id) DO NOTHING RETURNING request_id`;
const REVISION='SELECT coalesce(max(revision),0) AS revision FROM public.eric_app_changes WHERE workspace_id=$1::uuid';
const NAMESPACE='eric-os-wechat-fence-experiment-v1';

function conflict(code='wechat_target_conflict'):never {
  throw new AppFault(409,code,'同步状态不一致，内容仍保留在本机。');
}

function canonicalAttempt(value:any):any {
  // Fence the whole Raw/attempt identity, not only a particular payload hash:
  // a changed snapshot, source or valid payload must not reopen the old attempt.
  return {raw_id:value.raw_id,epoch:value.epoch,generation:value.generation};
}

export async function experimentalFenceKey(workspace:string,value:any):Promise<any> {
  const attempt=canonicalAttempt(value);
  const digest=await hash(NAMESPACE+'\0'+workspace+'\0'+stable(attempt));
  // UUID version 8 is deliberately outside the current public API's accepted
  // UUID versions 1-5. A collision with any existing receipt still fails closed.
  const bits=digest.slice(0,12)+'8'+digest.slice(13,16)+'8'+digest.slice(17,32);
  const id=bits.slice(0,8)+'-'+bits.slice(8,12)+'-'+bits.slice(12,16)+'-'+bits.slice(16,20)+'-'+bits.slice(20);
  return {id,digest,marker:{kind:'wechat_attempt_fence',version:1,digest}};
}

async function readFence(tx:any,workspace:string,key:any):Promise<boolean> {
  const rows=await tx.unsafe(RECEIPT_READ,[workspace,key.id]);
  if (rows.length>1) conflict('wechat_fence_conflict');
  if (!rows.length) return false;
  if (rows[0].request_hash!==key.digest || stable(rows[0].result)!==stable(key.marker))
    conflict('wechat_fence_conflict');
  return true;
}

async function addFence(tx:any,workspace:string,key:any):Promise<void> {
  if (await readFence(tx,workspace,key)) return;
  const inserted=await tx.unsafe(RECEIPT_INSERT,[workspace,key.id,key.digest,JSON.stringify(key.marker)]);
  if (inserted.length!==1 && !await readFence(tx,workspace,key)) conflict('wechat_fence_conflict');
}

async function workspaceState(tx:any,workspace:string,processor:string,epoch:string,allowRetired=false):Promise<number> {
  // The advisory lock protects a whole workspace. A stalled JavaScript caller
  // must not keep that lock indefinitely between SQL statements. PostgreSQL
  // terminates the database connection and rolls back the transaction;
  // callers must treat the result as unknown and reconcile before retrying.
  await tx.unsafe("SET LOCAL transaction_timeout='8s'",[]);
  await tx.unsafe("SET LOCAL idle_in_transaction_session_timeout='5s'",[]);
  await tx.unsafe("SET LOCAL statement_timeout='5s'",[]);
  await tx.unsafe("SET LOCAL lock_timeout='2s'",[]);
  await tx.unsafe(LOCK_SQL,[workspace]);
  if ((await tx.unsafe(PROCESSOR_SQL,[workspace,processor])).length!==1)
    throw new AppFault(401,'worker_revoked','同步凭据已失效，内容仍保留在本机。');
  const workers=await tx.unsafe(WORKER_SQL,[workspace]);
  if (workers.length!==1 || !UUID.test(String(workers[0].publisher_epoch)) ||
      (!allowRetired && String(workers[0].publisher_epoch)!==epoch)) conflict('stale_publisher');
  const current=Number(workers[0].source_generation);
  if (!Number.isSafeInteger(current) || current<0) throw new Error('invalid_generation');
  return current;
}

function validTargets(existing:any[],records:any[]):boolean {
  if (existing.length!==4) return false;
  const expected=new Map(records.map((r:any)=>[r.kind+'\u0000'+r.id,r.source_hash]));
  return existing.every((r:any)=>r.deleted===false && r.payload_matches===true &&
    expected.get(r.kind+'\u0000'+r.id)===r.source_hash);
}

export function experimentalPublisher(client:any) {
  return async (workspace:string,processor:string,input:any):Promise<any> => {
    if (!UUID.test(workspace) || !UUID.test(processor)) throw new AppFault(400,'invalid_wechat_scope','同步身份无效。');
    const body=await validateWechatBundle(input);
    if (body.generation>=Number.MAX_SAFE_INTEGER) conflict('stale_publisher');
    const key=await experimentalFenceKey(workspace,body);
    return await client.begin(async(tx:any)=>{
      const current=await workspaceState(tx,workspace,processor,body.epoch);
      if (await readFence(tx,workspace,key)) conflict('stale_publisher');
      const encoded=JSON.stringify(body.records);
      const existing=await tx.unsafe(EXISTING_SQL,[workspace,encoded]);
      if (existing.length) {
        // Existing SQL compares the full JSON payload and the source hash.
        if (!validTargets(existing,body.records)) conflict();
        return {state:'already_published'};
      }
      const inserted=await tx.unsafe(INSERT_SQL,[workspace,encoded,body.epoch,current]);
      if (inserted.length!==4) conflict();
      const result=await tx.unsafe(REVISION,[workspace]);
      if (result.length!==1 || !Number.isSafeInteger(Number(result[0].revision)))
        throw new Error('invalid_receipt');
      return {state:'published',revision:Number(result[0].revision)};
    });
  };
}

export function experimentalReconciler(client:any) {
  return async (workspace:string,processor:string,input:any):Promise<any> => {
    if (!UUID.test(workspace) || !UUID.test(processor)) throw new AppFault(400,'invalid_wechat_scope','同步身份无效。');
    const value=validateReconciliation(input);
    const proof=await hash(stable(value));
    const key=await experimentalFenceKey(workspace,value);
    return await client.begin(async(tx:any)=>{
      // A current authenticated processor may check and fence an old attempt.
      // This never changes the current session; the fence survives even if an
      // old session identifier is later reactivated by a delayed hello.
      await workspaceState(tx,workspace,processor,value.epoch,true);
      const priorFence=await readFence(tx,workspace,key);
      const existing=await tx.unsafe(RECONCILE_EXISTING_SQL,[workspace,JSON.stringify(value.records)]);
      if (existing.length) {
        if (priorFence || existing.length!==4) conflict();
        const expected=new Map(value.records.map((r:any)=>[r.kind+'\u0000'+r.id,r.source_hash]));
        for (const row of existing) {
          const expectedHash=expected.get(row.kind+'\u0000'+row.id);
          if (row.deleted!==false || expectedHash!==row.source_hash ||
              await hash(stable(row.payload))!==expectedHash ||
              (row.kind==='raw_item' && row.payload?.source_id!==value.source_id)) conflict();
        }
        return {state:'already_published',proof,generation:value.generation};
      }
      if (value.generation>=Number.MAX_SAFE_INTEGER-1) conflict('stale_publisher');
      if (!priorFence) await addFence(tx,workspace,key);
      return {state:'fenced_absent',proof,generation:value.generation+1};
    });
  };
}


type FactoryOptions={databaseUrl:string;clientFactory?:(url:string)=>Promise<any>};

async function receiptClientFor(databaseUrl:string):Promise<any> {
  // Isolated candidate only: use a dedicated pool for these two guarded
  // endpoints. Other API clients continue using their existing dependency.
  // Production Edge compatibility is not yet validated; this is not deployable.
  const {default:postgres}=await import('npm:postgres@4.0.0');
  // Deno 2.6's Node compatibility layer lacks the optional onread buffer API.
  return postgres(databaseUrl,{max:1,prepare:false,ssl:true,idle_timeout:20,max_lifetime:60,
    connect_timeout:5,fetch_types:false,onread:false,onnotice:()=>{},debug:false});
}

type PoolSlot={ready:Promise<any>;users:number;failed:boolean;disposed:boolean};

async function disposeRetiredPool(slot:PoolSlot,onClosed:()=>void):Promise<void> {
  if (slot.disposed) return;
  slot.disposed=true;
  let timer:ReturnType<typeof setTimeout>|undefined;
  try {
    let pool:any;
    try { pool=await slot.ready; }
    catch { onClosed(); return; } // The factory did not produce a pool.
    if (typeof pool?.end!=='function') return;
    // Do not expose driver errors or let cleanup delay an API response forever.
    // A later successful close can release the conservative capacity limit.
    const closing=Promise.resolve().then(()=>pool.end({timeout:1})).then(onClosed,()=>{});
    await Promise.race([
      closing,
      new Promise<void>(resolve=>{timer=setTimeout(resolve,1200);}),
    ]);
  } catch {
    // An unavailable or failed close stays counted until process recovery.
  } finally {
    if (timer!==undefined) clearTimeout(timer);
  }
}

function createReceiptPoolRunner(options:FactoryOptions) {
  let current:PoolSlot|undefined;
  const retired=new Set<PoolSlot>();
  return async (operation:(pool:any)=>Promise<any>):Promise<any> => {
    // Never create an unbounded number of replacement pools if cleanup hangs.
    if (!current && retired.size>=2)
      throw new AppFault(503,'scoped_publish_unavailable','同步保护暂时不可用，内容仍保留在本机。');
    const slot=current ||= {
      ready:Promise.resolve().then(()=>(options.clientFactory||receiptClientFor)(options.databaseUrl)),
      users:0,failed:false,disposed:false,
    };
    slot.users++;
    try {
      return await operation(await slot.ready);
    } catch(error) {
      if (error instanceof AppFault) throw error;
      if (!slot.failed) { slot.failed=true; retired.add(slot); }
      // A late failure from an older pool must not discard its replacement.
      if (current===slot) current=undefined;
      throw new AppFault(503,'scoped_publish_unavailable','同步结果暂时无法确认，内容仍保留在本机。');
    } finally {
      slot.users--;
      // Let other operations already using this pool settle before closing it.
      if (slot.failed && slot.users===0) await disposeRetiredPool(slot,()=>retired.delete(slot));
    }
  };
}

export function createReceiptFencePublisher(options:FactoryOptions) {
  const withClient=createReceiptPoolRunner(options);
  return async (workspace:string,processor:string,input:any):Promise<any> => {
    if (!UUID.test(workspace) || !UUID.test(processor))
      throw new AppFault(400,'invalid_wechat_scope','同步身份无效。');
    // Validate before creating a database connection, even for a retry.
    await validateWechatBundle(input);
    if (!options.databaseUrl)
      throw new AppFault(503,'scoped_publish_unavailable','同步保护暂时不可用，内容仍保留在本机。');
    return await withClient(pool=>experimentalPublisher(pool)(workspace,processor,input));
  };
}


export function createReceiptFenceReconciler(options:FactoryOptions) {
  const withClient=createReceiptPoolRunner(options);
  return async (workspace:string,processor:string,input:any):Promise<any> => {
    if (!UUID.test(workspace) || !UUID.test(processor))
      throw new AppFault(400,'invalid_wechat_scope','同步身份无效。');
    validateReconciliation(input);
    if (!options.databaseUrl)
      throw new AppFault(503,'scoped_publish_unavailable','同步保护暂时不可用，内容仍保留在本机。');
    return await withClient(pool=>experimentalReconciler(pool)(workspace,processor,input));
  };
}

