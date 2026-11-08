import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve,extname,sep} from 'node:path';

// Serve the exact production shell, then physically stop its origin. This
// avoids WebKit's native offline emulation also breaking local Blob/cache I/O.
export async function offlineOrigin(){
 const root=resolve('dist/client');
 const server=createServer(async(req,res)=>{
  try{
   const pathname=new URL(req.url,'http://127.0.0.1').pathname;
   if(pathname==='/offline-shell-prime'){res.writeHead(200,{'Content-Type':'text/html','Cache-Control':'no-store'});res.end('<!doctype html><title>Isolated offline shell preparation</title>');return;}
   const target=resolve(root,'.'+(pathname==='/'?'/index.html':pathname));
   if(!target.startsWith(root+sep)){res.writeHead(403);res.end();return;}
   const body=await readFile(target),mime={'.html':'text/html','.js':'text/javascript','.css':'text/css','.png':'image/png','.svg':'image/svg+xml','.webmanifest':'application/manifest+json','.json':'application/json'}[extname(target)]||'application/octet-stream';
   res.writeHead(200,{'Content-Type':mime,'Cache-Control':'no-store'});res.end(body);
  }catch{res.writeHead(404);res.end();}
 });
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
 return {url:'http://127.0.0.1:'+server.address().port,stop:()=>new Promise((resolve,reject)=>{if(!server.listening){resolve();return;}server.close(error=>error?reject(error):resolve());server.closeAllConnections();})};
}
