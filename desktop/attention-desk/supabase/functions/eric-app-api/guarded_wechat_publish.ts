import { AppFault, SHA, UUID, hash, stable } from './store.ts';

// Candidate only: this helper is not wired to a deployed API or local worker.
// Exact record shapes prevent hidden drafts, attachments or unrelated projections.
const RAW = ['id','source_id','kind','source_type','created_at','title','published_at','inbox_original_snapshot_id','inbox_snapshot_id','inbox_course_id','inbox_version','redacted_excerpt','minimization_policy'];
const SUMMARY = ['id','created_at','source','source_kind','input_kind','title','preview','preview_source','vision_notice','ocr_preview','processing_status','processing_stage','original_snapshot_id','snapshot_id','course','project_ref','classification','error','proposal_count','review_status','version','attachments','managed','needs_manual','redacted_excerpt'];
const DETAIL = [...SUMMARY,'original','review_available','review','extraction_quality','school_documents','receipts'];
const REVIEW = ['raw_snapshot_id','title_hint','status','version','created_at','updated_at','snapshot','item','source','extraction_id','draft','extraction','confirmed_event_ids','wechat_candidates','course_suggestions','source_update'];
const SNAPSHOT = ['id','raw_item_id','original_text','created_at','source_time','source_timezone','hash','blocks','attachments','attachment_jobs','document_warnings','redacted_excerpt','minimization_policy','wechat'];
const WECHAT = ['source','batch_id','messages','warnings','analysis','superseded_by','latest_snapshot_id'];
const BLOCK = ['raw_snapshot_id','block_id','source_type','ordinal','text','section','needs_review','attachment_id'];
const MESSAGE = ['snapshot_id','block_id','message_id','content_revision','raw_text','message_type','source_time','time_reliability','captured_at'];
const POLICY = 'selected-text-review-v1';

function invalid(code='invalid_wechat_scope'): never { throw new AppFault(400,code,'同步内容未通过范围核验，已保留在本机。'); }
function conflict(code='wechat_target_conflict'): never { throw new AppFault(409,code,'目标记录状态不一致，已暂停本次同步。'); }
function object(value: any, names: string[]): boolean {
  return Boolean(value && typeof value==='object' && !Array.isArray(value) &&
    stable(Object.keys(value).sort())===stable([...names].sort()));
}
function empty(value: any): boolean { return Array.isArray(value) && value.length===0; }
function equal(a: any,b: any): boolean { return stable(a)===stable(b); }
function integer(value: any,min=0): boolean { return Number.isSafeInteger(value) && value>=min; }
function uuid(value: any): boolean { return typeof value==='string' && UUID.test(value); }
function timestamp(value: any): boolean {
  return typeof value==='string' && /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
    Number.isFinite(Date.parse(value));
}
const GENERIC_POLICY=/^\s*(?:[0-9]{1,2}[.、]\s*)?(?:[!！❗\ufe0f]\s*)*(?:报名前需确认好本人空闲时间[，,]\s*)?(?:报名成功后不得随意更改[，,]\s*)?(?:随意取消活动|无故缺席|违规报名)(?:将会|将|会)(?:被)?(?:拉入|列入)黑名单[。；;！!]?\s*$/;
const GENERIC_SHORT=new Set(['通知','注意事项','温馨提示','大家好','晚上好','上午好','下午好','谢谢','谢谢配合','工作时间','工作地点','服装要求','报名方式']);
function privacyScanText(text:string):string|null {
  const lines=text.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/),matched=lines.map(line=>GENERIC_POLICY.test(line));
  if(matched.some(Boolean)) {
    for(let i=0;i<lines.length;i++) {
      const short=lines[i].match(/^\s*(?:[0-9]{1,2}[.、]\s*)?([\u4e00-\u9fff]{2,4})[。；;！!]?\s*$/);
      if(!matched[i]&&short&&!GENERIC_SHORT.has(short[1]))return null;
    }
  }
  return lines.map((line,i)=>matched[i]?'通用报名纪律要求':line).join('\n');
}
function safeText(value: any, scanAudience=false): boolean {
  if (typeof value!=='string' || value.length>16000) return false;
  if (/&(?:#(?:x[0-9a-f]{1,6}|[0-9]{1,7});?|[a-z][a-z0-9]{1,31};)|%[0-9a-f]{2}|\\u[0-9a-f]{4}/i.test(value)) return false;
  // Remove only the exact marker emitted by the local redactor before checking
  // for residual sensitive text. Joining the surrounding text first also
  // prevents a marker from splitting and hiding a phone number or email.
  // A lookalike or encoded marker is not trusted and remains subject to checks.
  let canonical=value.replaceAll('[姓名已去除]','').normalize('NFKC').replace(/\p{Cf}/gu,'');
  if (canonical.includes('[敏感内容待核对]')) return false;
  const scanned=privacyScanText(canonical);
  if(scanned===null)return false;
  canonical=scanned;
  if(scanAudience)canonical=canonical.replaceAll('有意参加的同学','');
  const unnamed=canonical.replace(/各位同学|全体同学|所有同学|同学们|老师们/g,'');
  if (/@[^\s@，,：:。；;！!\n]{1,40}/.test(canonical) ||
      /[a-z0-9._%+-]+[ \t]*@[ \t]*[a-z0-9-]+(?:[ \t]*\.[ \t]*[a-z0-9-]+)+/i.test(canonical) ||
      /(?<!\d)(?:\+?86[ \t.()\-]*)?1[ \t.()\-]*[3-9](?:[ \t.()\-]*\d){9}(?!\d)/.test(canonical)) return false;
  return !/(?:https?:\/\/|wxid_[a-z0-9_]+|[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}|(?<!\d)(?:\+?86[ -]*)?1[3-9](?:[ -]*\d){9}(?!\d)|(?<!\d)\d{6,}[x]?(?!\d)|(?:[a-z]:\\|\\\\|\/users\/|\/home\/)|姓名|学号|身份证|银行卡|病历|诊断|医保|家庭住址|政治面貌|心理咨询|贫困生|处分名单|接龙|名单|联系人|负责老师)/i.test(canonical) &&
    !/[\u4e00-\u9fff]{2,4}(?:同学|老师|教授)/.test(unnamed);
}

export async function validateWechatBundle(body: any): Promise<any> {
  if (!object(body,['raw_id','snapshot_id','source_id','records','epoch','generation']) ||
      !uuid(body.raw_id) || !uuid(body.snapshot_id) || !uuid(body.epoch) || !integer(body.generation) ||
      typeof body.source_id!=='string' || !/^wechat:[A-Za-z0-9_-]{1,80}$/.test(body.source_id) ||
      !Array.isArray(body.records) || body.records.length!==4) invalid();
  const expected: Record<string,string> = {raw_item:body.raw_id,inbox_item:body.raw_id,inbox:body.raw_id,raw_review:body.snapshot_id};
  const records: Record<string,any> = {};
  for (const row of body.records) {
    if (!object(row,['kind','id','payload','source_hash']) || !(row.kind in expected) || records[row.kind] ||
        row.id!==expected[row.kind] || typeof row.source_hash!=='string' || !SHA.test(row.source_hash) ||
        row.source_hash!==await hash(stable(row.payload))) invalid();
    records[row.kind]=row.payload;
  }
  const raw=records.raw_item,summary=records.inbox_item,detail=records.inbox,review=records.raw_review;
  if (!object(raw,RAW) || !object(summary,SUMMARY) || !object(detail,DETAIL) || !object(review,REVIEW)) invalid();
  // Fixed display hint only; no media inspection, decoded content or old replay.
  const messages=review?.snapshot?.wechat?.messages;
  const cutoff=Date.parse('2026-11-08T13:52:00+00:00');
  const scanNotice=Array.isArray(messages) && messages.length>0 &&
    messages.every((m:any)=>m && m.message_type==='text' && m.time_reliability==='reliable' &&
      ['source_time','captured_at'].every(k=>timestamp(m[k]) && Date.parse(m[k])>=cutoff)) &&
    messages.some((m:any)=>typeof m.raw_text==='string' && /扫码|扫描\s*(?:二维)?码|二维码/.test(m.raw_text));
  const safe=(value:any)=>safeText(value,scanNotice);
  if (raw.id!==body.raw_id || raw.source_id!==body.source_id || raw.kind!=='wechat_batch' || raw.source_type!=='wechat_batch' ||
      raw.inbox_snapshot_id!==body.snapshot_id || raw.inbox_original_snapshot_id!==body.snapshot_id ||
      raw.redacted_excerpt!==true || raw.minimization_policy!==POLICY || !integer(raw.inbox_version,1) || !timestamp(raw.created_at) ||
      !safe(raw.title) || !raw.title.startsWith('【脱敏节选】')) invalid();
  if (summary.id!==body.raw_id || summary.created_at!==raw.created_at || summary.source!=='微信通知（脱敏节选）' || summary.source_kind!=='wechat' ||
      summary.input_kind!=='text' || summary.preview_source!=='text' || summary.title!==raw.title || !safe(summary.preview) ||
      summary.original_snapshot_id!==body.snapshot_id || summary.snapshot_id!==body.snapshot_id ||
      summary.redacted_excerpt!==true || summary.needs_manual!==true || summary.managed!==false ||
      summary.processing_status!=='needs_review' || summary.processing_stage!=='review' || !['pending','archived'].includes(summary.review_status) ||
      summary.vision_notice!==null || summary.ocr_preview!==null || summary.project_ref!==null ||
      summary.classification!==null || summary.error!==null || summary.proposal_count!==0 || !empty(summary.attachments) ||
      !integer(summary.version,1) || summary.version!==raw.inbox_version) invalid();
  for (const key of SUMMARY) if (!equal(summary[key],detail[key])) invalid();
  if (detail.review_available!==true || !empty(detail.school_documents) || !empty(detail.receipts) ||
      !object(detail.extraction_quality,['files','needs_review','reasons','meaning']) ||
      !empty(detail.extraction_quality.files) || detail.extraction_quality.needs_review!==true ||
      !equal(detail.extraction_quality.reasons,['redacted_excerpt']) || detail.extraction_quality.meaning!=='脱敏文字节选') invalid();
  // Course metadata is a separate local association, outside this text scope.
  if (summary.course!==null || raw.inbox_course_id!==null) invalid();
  if (review.raw_snapshot_id!==body.snapshot_id || review.title_hint!==raw.title || review.status!==summary.review_status ||
      !timestamp(review.created_at) || !timestamp(review.updated_at) ||
      !integer(review.version,1) || review.extraction_id!==null || review.draft!==null || review.extraction!==null ||
      !empty(review.confirmed_event_ids) || !empty(review.wechat_candidates) || !object(review.course_suggestions,[]) ||
      !object(review.source_update,['events','proposals','requires_confirmation']) || !empty(review.source_update.events) ||
      !empty(review.source_update.proposals) || review.source_update.requires_confirmation!==false ||
      !object(review.source,['id','kind','name']) || review.source.id!==body.source_id || review.source.kind!=='wechat' ||
      review.source.name!=='微信通知（脱敏节选）' || !equal(review.item,raw) || !equal(detail.review,review)) invalid();
  const snapshot=review.snapshot;
  const warnings=[{code:'redacted_excerpt',message:'这是经最小化的文字节选，完整原文仅保留在本机。'}];
  if(scanNotice)warnings.push({code:'qr_in_original_chat',message:'需扫码，二维码请返回原微信群查看'});
  if (!object(snapshot,SNAPSHOT) || !equal(detail.original,snapshot) || snapshot.id!==body.snapshot_id ||
      snapshot.raw_item_id!==body.raw_id || snapshot.redacted_excerpt!==true || snapshot.minimization_policy!==POLICY ||
      !timestamp(snapshot.created_at) || !['Asia/Shanghai','UTC','Etc/UTC'].includes(snapshot.source_timezone) ||
      !safe(snapshot.original_text) || snapshot.hash!==await hash(snapshot.original_text) ||
      !empty(snapshot.attachments) || !empty(snapshot.attachment_jobs) ||
      !equal(snapshot.document_warnings,warnings)) invalid();
  const wx=snapshot.wechat;
  if (!object(wx,WECHAT) || wx.source!=='wechat' || !uuid(wx.batch_id) || !empty(wx.analysis) ||
      !equal(wx.warnings,['redacted_excerpt']) || wx.superseded_by!==null || wx.latest_snapshot_id!==body.snapshot_id ||
      !Array.isArray(wx.messages) || wx.messages.length<1 || wx.messages.length>100 ||
      !Array.isArray(snapshot.blocks) || snapshot.blocks.length!==wx.messages.length) invalid();
  const ids=new Set<string>(), blocks=new Set<string>();
  for (let index=0;index<wx.messages.length;index++) {
    const message=wx.messages[index],block=snapshot.blocks[index];
    if (!object(message,MESSAGE) || !object(block,BLOCK) || message.snapshot_id!==body.snapshot_id ||
        block.raw_snapshot_id!==body.snapshot_id || !uuid(message.message_id) || ids.has(message.message_id) ||
        message.block_id!=='WX_'+message.message_id || blocks.has(message.block_id) || block.block_id!==message.block_id ||
        message.message_type!=='text' || block.source_type!=='text' || block.section!=='脱敏节选' ||
        block.needs_review!==true || block.attachment_id!==null || !integer(block.ordinal) ||
        !integer(message.content_revision) || message.time_reliability!=='reliable' ||
        !timestamp(message.source_time) || !timestamp(message.captured_at) || !safe(message.raw_text) || !message.raw_text.trim() ||
        block.text!==message.raw_text) invalid();
    ids.add(message.message_id);blocks.add(message.block_id);
  }
  if (snapshot.original_text!==wx.messages.map((message: any)=>message.raw_text).join('\n') ||
      summary.preview!==Array.from(snapshot.original_text).slice(0,160).join('') ||
      raw.published_at!==wx.messages[0].source_time || snapshot.source_time!==wx.messages[0].source_time) invalid();
  return body;
}

export const LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended('eric-app:'||$1::text,0))";
export const PROCESSOR_SQL = 'SELECT id FROM public.eric_app_processors WHERE workspace_id=$1::uuid AND id=$2::uuid AND revoked_at IS NULL FOR SHARE';
export const WORKER_SQL = 'SELECT publisher_epoch,source_generation FROM public.eric_app_workers WHERE workspace_id=$1::uuid FOR UPDATE';
export const EXISTING_SQL = `SELECT r.kind,r.id,r.source_hash,r.deleted,(r.payload=p.payload) AS payload_matches
  FROM public.eric_app_records r JOIN jsonb_to_recordset(($2::text)::jsonb) AS p(kind text,id text,payload jsonb,source_hash text)
    ON r.kind=p.kind AND r.id=p.id WHERE r.workspace_id=$1::uuid FOR UPDATE OF r`;
export const INSERT_SQL = `INSERT INTO public.eric_app_records(workspace_id,kind,id,payload,source_hash,deleted,source_epoch,source_generation)
  SELECT $1::uuid,p.kind,p.id,p.payload,p.source_hash,false,$3::uuid,$4::bigint
  FROM jsonb_to_recordset(($2::text)::jsonb) AS p(kind text,id text,payload jsonb,source_hash text)
  ON CONFLICT(workspace_id,kind,id) DO NOTHING RETURNING kind,id`;
// Binding an already-serialized JSON string directly as jsonb causes
// postgres.js to quote it a second time. Bind text first, then cast in SQL.
export const PUBLISH_SQL = 'SELECT public.eric_app_publish_records($1::uuid,($2::text)::jsonb,$3::uuid,$4::bigint) AS revision';

export async function clientFor(databaseUrl: string): Promise<any> {
  const { default: postgres } = await import('npm:postgres@3.4.7');
  return postgres(databaseUrl, {max:1,prepare:false,ssl:true,idle_timeout:20,max_lifetime:60,
    connect_timeout:5,fetch_types:false,onnotice:()=>{},debug:false});
}

export function createGuardedWechatPublisher(options: {databaseUrl:string;clientFactory?:(url:string)=>Promise<any>}) {
  let client: Promise<any> | undefined;
  return async (workspace: string, processor: string, value: any): Promise<any> => {
    if (!uuid(workspace) || !uuid(processor)) invalid();
    const body=await validateWechatBundle(value);
    if (!options.databaseUrl) throw new AppFault(503,'scoped_publish_unavailable','同步保护暂时不可用，内容仍保留在本机。');
    try {
      client ||= (options.clientFactory || clientFor)(options.databaseUrl);
      const sql=await client, encoded=JSON.stringify(body.records);
      return await sql.begin(async (tx: any) => {
        await tx.unsafe("SET LOCAL statement_timeout='5s'",[]);
        await tx.unsafe("SET LOCAL lock_timeout='2s'",[]);
        await tx.unsafe(LOCK_SQL,[workspace]);
        // Recheck inside the transaction. A revocation between HTTP
        // authentication and the write must not authorize a new publication.
        if ((await tx.unsafe(PROCESSOR_SQL,[workspace,processor])).length!==1)
          throw new AppFault(401,'worker_revoked','同步凭据已失效，内容仍保留在本机。');
        const workers=await tx.unsafe(WORKER_SQL,[workspace]);
        if (workers.length!==1 || String(workers[0].publisher_epoch)!==body.epoch) conflict('stale_publisher');
        const current=Number(workers[0].source_generation);
        if (!Number.isSafeInteger(current) || current<0 || current>=Number.MAX_SAFE_INTEGER)
          throw new Error('invalid_generation');
        // A guarded message may use the current cycle or its next cycle.  An
        // unexplained jump must not advance the shared workspace publisher.
        if (body.generation<current || body.generation>current+1) conflict('stale_publisher');
        const existing=await tx.unsafe(EXISTING_SQL,[workspace,encoded]);
        if (existing.length) {
          const expected=new Map(body.records.map((r: any)=>[r.kind+'\u0000'+r.id,r.source_hash]));
          if (existing.length!==4 || existing.some((r: any)=>r.deleted!==false || r.payload_matches!==true ||
              expected.get(r.kind+'\u0000'+r.id)!==r.source_hash)) conflict();
          return {state:'already_published'};
        }
        const inserted=await tx.unsafe(INSERT_SQL,[workspace,encoded,body.epoch,body.generation]);
        if (inserted.length!==4) conflict(); // Throw rolls back every insert, including partial collisions.
        // The four guarded inserts already created the records. Reuse the
        // existing RPC with an empty batch for its epoch/generation metadata;
        // replaying the records would consume extra revision sequence values.
        const result=await tx.unsafe(PUBLISH_SQL,[workspace,'[]',body.epoch,body.generation]);
        if (result.length!==1 || !Number.isSafeInteger(Number(result[0].revision))) throw new Error('invalid_receipt');
        return {state:'published',revision:Number(result[0].revision)};
      });
    } catch (error) {
      if (error instanceof AppFault) throw error;
      client=undefined;
      throw new AppFault(503,'scoped_publish_unavailable','同步结果暂时无法确认，已保留原始内容。');
    }
  };
}

