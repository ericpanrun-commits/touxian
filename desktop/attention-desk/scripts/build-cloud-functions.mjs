import {build} from 'esbuild';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

for(const name of ['eric-app-api','eric-notes-ingest']) {
  const output=resolve('artifacts/cloud-functions',name,'index.ts');
  const bundle=await build({entryPoints:[resolve('supabase/functions',name,'index.ts')],bundle:true,write:false,format:'esm',platform:'neutral',target:'es2022',legalComments:'inline'});
  await mkdir(resolve('artifacts/cloud-functions',name),{recursive:true});
  await writeFile(output,bundle.outputFiles[0].contents);
  process.stdout.write(JSON.stringify({function:name,bytes:bundle.outputFiles[0].contents.length,output})+'\n');
}
