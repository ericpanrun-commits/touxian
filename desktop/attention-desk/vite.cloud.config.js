import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {fileURLToPath} from 'node:url';
import {cp,mkdir,readFile,readdir,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve,relative} from 'node:path';
const root=fileURLToPath(new URL('.',import.meta.url)),output=resolve(root,'dist/client');
async function files(directory){const result=[];for(const item of await readdir(directory,{withFileTypes:true})){const path=resolve(directory,item.name);if(item.isDirectory())result.push(...await files(path));else result.push(path);}return result;}
export default defineConfig({root:resolve(root,'cloud'),publicDir:resolve(root,'public/cloud'),plugins:[react(),{name:'eric-offline-shell',async closeBundle(){
  const pdf=resolve(root,'node_modules/pdfjs-dist'),{version:pdfVersion}=JSON.parse(await readFile(resolve(pdf,'package.json'),'utf8'));
  const pdfOutput=resolve(output,'pdfjs-'+pdfVersion);await mkdir(pdfOutput,{recursive:true});
  for(const directory of ['cmaps','standard_fonts','wasm','iccs'])await cp(resolve(pdf,directory),resolve(pdfOutput,directory),{recursive:true});
  const source=await readFile(resolve(root,'src/cloud/service-worker.js'),'utf8');
  const paths=(await files(output)).filter(path=>!path.endsWith('sw.js')&&!relative(output,path).startsWith('_')).sort(),digest=createHash('sha256').update(source);for(const path of paths)digest.update(await readFile(path));
  const version=digest.digest('hex').slice(0,16),urls=paths.map(path=>'/'+relative(output,path).replaceAll('\\','/'));
  const assets=urls.filter(path=>!path.endsWith('.html'));
  const shell=assets.filter(path=>!path.startsWith('/pdfjs-')&&!/^\/assets\/(PdfSourcePage|pdf[.-])/.test(path));
  const html=await readFile(resolve(output,'index.html'),'utf8'),entry=/type="module"[^>]*src="([^"]+)"/.exec(html)?.[1];
  if(!entry)throw new Error('Cloud shell entry missing');
  const worker='const ERIC_SW='+JSON.stringify({cache:'eric-os-shell-nav2-'+version,files:assets,shell,entry})+';\n'+source;
  await writeFile(resolve(output,'sw.js'),worker);
}}],build:{outDir:output,emptyOutDir:true,rollupOptions:{output:{manualChunks:{react:['react','react-dom'],cloud:['@supabase/supabase-js','dexie']}}}},server:{host:'127.0.0.1',port:5175}});
