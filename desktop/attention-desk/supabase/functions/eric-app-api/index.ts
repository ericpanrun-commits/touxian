import {createHandler} from './handler.ts';
const named=JSON.parse(Deno.env.get('SUPABASE_SECRET_KEYS')||'{}');
const handler=createHandler({base:Deno.env.get('SUPABASE_URL')||'',secret:named.default||Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')||'',
  officialHealth: (() => {
    let reader: ((workspace: string) => Promise<any>) | undefined;
    return async (workspace: string) => {
      reader ||= (await import('./official_health_db.ts')).createOfficialHealthReader({databaseUrl:Deno.env.get('SUPABASE_DB_URL')||''});
      return await reader(workspace);
    };
  })(),
  visionSecret:Deno.env.get('ERIC_VISION_BOT_SECRET')||'',
  origins:['https://eric-os-workbench-ericpanrun.choicechar22.chatgpt.site',...(Deno.env.get('ERIC_APP_ALLOWED_ORIGINS')||'').split(',').map((x: string)=>x.trim()).filter(Boolean)]});
Deno.serve(handler);
