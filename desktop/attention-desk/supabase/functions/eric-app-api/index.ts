import {createHandler} from './handler.ts';
import {createHealthOverviewReader} from './health_overview.ts';
const named=JSON.parse(Deno.env.get('SUPABASE_SECRET_KEYS')||'{}');
const handler=createHandler({base:Deno.env.get('SUPABASE_URL')||'',secret:named.default||Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')||'',
  healthOverview:createHealthOverviewReader({token:Deno.env.get('ERIC_HEALTH_OVERVIEW_TOKEN')||'',owner:Deno.env.get('ERIC_HEALTH_OWNER_ID')||'',workspace:Deno.env.get('ERIC_HEALTH_WORKSPACE_ID')||'',mode:Deno.env.get('HEALTH_OVERVIEW_MODE')||'off'}),
  officialHealth: (() => {
    let reader: ((workspace: string) => Promise<any>) | undefined;
    return async (workspace: string) => {
      reader ||= (await import('./official_health_db.ts')).createOfficialHealthReader({databaseUrl:Deno.env.get('SUPABASE_DB_URL')||''});
      return await reader(workspace);
    };
  })(),
  guardedWechatPublish: (() => {
    let writer: ((workspace: string, processor: string, body: any) => Promise<any>) | undefined;
    return async (workspace: string, processor: string, body: any) => {
      writer ||= (await import('./receipt_fence_experiment.ts')).createReceiptFencePublisher({databaseUrl:Deno.env.get('SUPABASE_DB_URL')||''});
      return await writer(workspace,processor,body);
    };
  })(),
  guardedWechatSession: (() => {
    let session: ((workspace: string, processor: string, body: any) => Promise<any>) | undefined;
    return async (workspace: string, processor: string, body: any) => {
      session ||= (await import('./guarded_wechat_session.ts')).createGuardedWechatSession({databaseUrl:Deno.env.get('SUPABASE_DB_URL')||''});
      return await session(workspace,processor,body);
    };
  })(),
  guardedWechatReconcile: (() => {
    let reconcile: ((workspace: string, processor: string, body: any) => Promise<any>) | undefined;
    return async (workspace: string, processor: string, body: any) => {
      reconcile ||= (await import('./receipt_fence_experiment.ts')).createReceiptFenceReconciler({databaseUrl:Deno.env.get('SUPABASE_DB_URL')||''});
      return await reconcile(workspace,processor,body);
    };
  })(),
  visionSecret:Deno.env.get('ERIC_VISION_BOT_SECRET')||'',
  origins:['https://eric-os-workbench-ericpanrun.choicechar22.chatgpt.site',...(Deno.env.get('ERIC_APP_ALLOWED_ORIGINS')||'').split(',').map((x: string)=>x.trim()).filter(Boolean)]});
Deno.serve(handler);


