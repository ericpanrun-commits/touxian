import { createHandler } from './handler.ts';
import { SHORTCUT_BASE64, SHORTCUT_SHA256 } from './shortcut.ts';
import { CLEANUP_BASE64, CLEANUP_SHA256 } from './cleanup-shortcut.ts';
import { CloudStore } from '../eric-app-api/store.ts';
import { shortcutArchive } from '../eric-app-api/archive.ts';

const named = JSON.parse(Deno.env.get('SUPABASE_SECRET_KEYS') || '{}');
const secret = named.default || Deno.env.get('SUPABASE_SERVICE_ROLE_KEY') || '';
const base = Deno.env.get('SUPABASE_URL') || '';
const handler = createHandler({ base, secret, archive:shortcutArchive(new CloudStore({base,secret})) });
Deno.serve((req: Request) => {
  const route = new URL(req.url).pathname.replace(/^.*\/eric-notes-ingest/, '');
  if (req.method === 'GET' && ['/shortcut', '/shortcut-cleanup'].includes(route)) {
    // This public installation asset contains only generic actions, never a capability.
    const cleanup = route === '/shortcut-cleanup';
    const data = Uint8Array.from(atob(cleanup ? CLEANUP_BASE64 : SHORTCUT_BASE64), c => c.charCodeAt(0));
    return new Response(data, { headers: {
      'Content-Type': 'application/x-apple-shortcut',
      'Content-Disposition': "attachment; filename=EricOSNotesIngest.shortcut; filename*=UTF-8''" + encodeURIComponent(cleanup ? 'Eric OS 清理已接收缓存.shortcut' : 'Eric OS 课堂笔记.shortcut'),
      'Cache-Control': 'public, max-age=86400',
      'X-Content-Type-Options': 'nosniff',
      'ETag': '"' + (cleanup ? CLEANUP_SHA256 : SHORTCUT_SHA256) + '"',
    } });
  }
  return handler(req);
});
