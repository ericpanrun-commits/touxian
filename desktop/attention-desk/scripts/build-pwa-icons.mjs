// Rasterize the existing code-native check icon; no generated illustration assets.
import {chromium} from 'playwright';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const icon=await readFile(resolve('public/cloud/icon.svg'),'utf8'),browser=await chromium.launch({headless:true});
try {
  const page=await browser.newPage({viewport:{width:512,height:512},deviceScaleFactor:1});
  for(const [size,maskable] of [[192,false],[512,false],[512,true]]) {
    await page.setContent('<style>body{margin:0}svg{display:block;width:'+size+'px;height:'+size+'px}</style>'+(maskable?icon.replace('rx="112"','rx="0"'):icon));
    await page.locator('svg').screenshot({path:resolve('public/cloud',maskable?'icon-maskable-512.png':'icon-'+size+'.png'),omitBackground:true});
  }
} finally {await browser.close();}
