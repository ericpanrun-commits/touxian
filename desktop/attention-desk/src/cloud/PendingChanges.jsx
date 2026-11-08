import usePersonalResource from '../personal/usePersonalResource';

function destination(key){const at=key.indexOf(':'),kind=key.slice(0,at),id=key.slice(at+1);return {view:({event:'event',inbox:'inbox',raw_review:'raw-review',lecture:'lecture',learning_capture:'learning',topic:'learning',facade:'learning',course:'courses'})[kind]||'recent',id:id.startsWith('new:')||kind==='facade'?null:id};}
function label(item){if(item.path.includes('/answers'))return '学习作答';if(item.path.includes('/history/'))return '历史学习记录';if(item.path.includes('/authorization'))return '处理范围设置';if(item.path.includes('/sync'))return '学习通读取';if(item.path.includes('/feedback'))return '复盘反馈';if(item.path.includes('/generate'))return '课堂复盘';if(item.path.includes('/sections'))return '教材关联';if(item.path.startsWith('/raw/'))return '内容确认';if(item.path.startsWith('/inbox/'))return '收件箱修改';if(item.path.startsWith('/events/'))return '事项修改';return '资料修改';}
export default function PendingChanges({navigate}){
  const resource=usePersonalResource('/activity');
  const items=resource.data?.items||[];
  if(!items.length)return null;
  const confirmations=items.filter(item=>['needs_confirmation','failed'].includes(item.state));
  return <details className="cloud-pending desk-disclosure" open={confirmations.length>0}><summary>{confirmations.length?`${confirmations.length} 项修改需要核对`:`${items.length} 项修改已保存，正在接续处理`}</summary><ul className="cloud-link-list">{items.map(item=><li key={item.id}><span><strong>{label(item)}</strong><small>{['needs_confirmation','failed'].includes(item.state)?item.result?.message||item.error?.message||'请打开最新内容核对后继续。':item.state==='processing'?'正在处理，完成后会自动更新。':'已在云端保存，整理电脑上线后会自动继续。'}</small></span><button type="button" className="personal-text-button" onClick={()=>{const target=destination(item.resource_key);navigate(target.view,target.id);}}>打开查看</button></li>)}</ul></details>;
}
