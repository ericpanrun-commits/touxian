import { useEffect, useState } from 'react';
import { Field } from '../personal/Forms';

const notesParameters = new URLSearchParams(window.location.hash.slice(1));
const legacyKey = notesParameters.get('key') || '';
const persistent = notesParameters.get('days') === '90';
const legacyCloud = notesParameters.get('cloud') === '1';
const setupToken = new URLSearchParams(window.location.search).get('setup') || '';
const cloudEndpoint = 'https://mantuehuzptactymjpqg.supabase.co/functions/v1/eric-notes-ingest/upload';
if (window.location.pathname === '/notes-setup' && window.location.hash) window.history.replaceState({}, '', '/notes-setup');

function CopyValue({ label, value, secret = false }) {
  const [message, setMessage] = useState('');
  const [revealed,setRevealed] = useState(false);
  const copy = async () => {
    let copied = false;
    try { if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(value); copied = true; } } catch {}
    if (!copied) {
      const input = document.createElement('textarea'); input.value = value; input.style.position = 'fixed'; input.style.opacity = '0'; document.body.appendChild(input);
      try { input.focus(); input.select(); input.setSelectionRange(0, value.length); copied = document.execCommand('copy'); } catch {} finally { input.remove(); }
    }
    setMessage(copied ? '已复制' : '请长按输入框复制。');
  };
  return <div className="notes-setup-copy"><Field name={secret ? 'notes-key' : 'notes-endpoint'} label={label}><input type={secret && !revealed ? 'password' : 'text'} value={value} readOnly autoComplete="off" onFocus={event => event.target.select()} /></Field><div className="personal-form-actions"><button type="button" className="button button-quiet" onClick={copy}>复制{label}</button>{secret ? <button type="button" className="button button-quiet" aria-pressed={revealed} onClick={()=>setRevealed(value=>!value)}>{revealed ? '隐藏' : '显示'}{label}</button> : null}</div><p className="personal-meta" role="status">{message}</p></div>;
}

export default function NotesShortcutSetup() {
  const [connection,setConnection] = useState({key:legacyKey,cloud:legacyCloud});
  const [loading,setLoading] = useState(Boolean(setupToken));
  const [error,setError] = useState('');
  const [retry,setRetry] = useState(0);
  useEffect(()=>{
    if(!setupToken)return;
    const controller=new AbortController();
    const read=async()=>{
      setLoading(true);setError('');
      try {
        if(!/^[A-Za-z0-9_-]{43}$/.test(setupToken))throw Error('配置码无效，请在电脑重新点击“配置笔记分享”。');
        const response=await fetch('/notes-connection?'+new URLSearchParams({setup:setupToken}),{signal:controller.signal,cache:'no-store',credentials:'omit',redirect:'error',referrerPolicy:'no-referrer'});
        const value=await response.json();
        if(!response.ok)throw Error(value.error?.message || '暂时无法读取连接，请确认电脑工作台正在运行后重试。');
        if(value.endpoint!==cloudEndpoint || !/^enote_[A-Za-z0-9_-]{43}$/.test(value.upload_key||''))throw Error('配置连接无法验证，请在电脑重新生成配置码。');
        if(!controller.signal.aborted)setConnection({key:value.upload_key,cloud:true});
      } catch(failure) {if(!controller.signal.aborted)setError(failure.message);}
      finally {if(!controller.signal.aborted)setLoading(false);}
    };
    read();return()=>controller.abort();
  },[retry]);
  const notesKey=connection.key,cloud=connection.cloud;
  const valid = /^[A-Za-z0-9_-]{32,128}$/.test(notesKey) && (!cloud || (setupToken || notesParameters.get('endpoint') === cloudEndpoint) && /^enote_[A-Za-z0-9_-]{43}$/.test(notesKey));
  if(setupToken && (loading || error))return <main className="mobile-collector"><header className="mobile-heading"><h1>课堂笔记 → Eric OS</h1><p>首次连接</p></header><section className="personal-section">{loading ? <p role="status">正在读取笔记连接…</p> : <><p role="alert">{error}</p><button type="button" className="button button-primary" onClick={()=>setRetry(value=>value+1)}>重新读取连接</button></>}</section></main>;
  if (valid && cloud) return <main className="mobile-collector"><header className="mobile-heading"><h1>课堂笔记 → Eric OS</h1><p>外网可用 · 电脑可以离线</p></header>
    <section className="personal-section"><h2>分享后，看到“已接收”就完成</h2><p>云端先私密保存原件。电脑上线后自动取回，继续关联课程、课次和复盘。</p></section>
    <section className="personal-section"><h2>安装分享捷径</h2><p>先复制下方“笔记连接”，再安装捷径，在首次设置时粘贴一次。接收地址和待发送步骤已经配好。</p><CopyValue label="笔记连接" value={notesKey} secret/><div className="personal-form-actions"><a className="button button-primary" href={cloudEndpoint.replace('/upload','/shortcut')+'?v=fc1530b9'} referrerPolicy="no-referrer">安装 Eric OS 课堂笔记</a></div><p className="personal-meta">首次运行时，允许访问本机待发送文件夹和接收地址。以后在备忘录选择“发送副本 → Eric OS 课堂笔记”。上传成功后保留已确认缓存，无需每次删除。连接只能上传笔记；在电脑“手机收集 → 笔记分享设备”中可撤销。</p></section>
    <details className="personal-section desk-disclosure"><summary>接收地址</summary><CopyValue label="接收地址" value={cloudEndpoint}/></details>
    <details className="personal-section desk-disclosure"><summary>手动设置备用步骤</summary><ol className="notes-setup-steps">
      <li>新建或编辑“Eric OS 课堂笔记”，打开“在共享表单中显示”，接收文件、图像和富文本。</li>
      <li>添加“如果”：快捷指令输入“有任何值”时，添加“制作 PDF”。已有 PDF 使用原文件，以便重复分享时保留相同内容。</li>
      <li>给待发送文件使用“生成 UUID”加原文件名作为新名称，保存到本机的“Eric OS 待发送”文件夹。首次选择一次文件夹，关闭“询问存储位置”，随后结束这个“如果”。</li>
      <li>添加“获取文件夹内容”，选择同一个待发送文件夹，再添加“重复每一项”。日后直接运行此捷径，也会重试这里尚未发送的笔记。</li>
      <li>在重复内添加“获取 URL 内容”：地址复制下方“接收地址”，方法选 <code>POST</code>，请求体选<strong>文件</strong>，值选<strong>重复项目</strong>。标头 <code>X-Eric-Notes-Key</code> 的值复制下方“笔记连接”。</li>
      <li>为标头 <code>X-Eric-Filename</code> 填入经过“URL 编码”的重复项目名称；为 <code>X-Eric-Shared-At</code> 填入该文件创建日期的 ISO 8601 格式，保留首次分享时间。</li>
      <li>用“获取词典值”读取返回内容的 <code>saved</code>。只有它为真，才删除本机待发送副本；否则保留文件并显示返回的提示。关闭删除动作的逐次确认，原始备忘录不参与删除。</li>
      <li>结束重复后，在本次确有文件且全部成功时显示一次通知<strong>“已接收”</strong>。手机断网时待发送副本会保留，下次分享或运行捷径会重试。</li>
    </ol><p>日常使用：备忘录 → 分享 → 发送副本 → Eric OS 课堂笔记。PDF 附件从附件自身分享，保留完整页数。</p></details>
    <details className="personal-section desk-disclosure"><summary>断网后的自动重试</summary><p>快捷指令会先保存待发送副本。iOS 不保证任意网络失败后在后台继续执行；下次分享或主动运行捷径会自动重试整批文件。也可首次设置一个“加入任意 Wi-Fi”时运行此捷径的个人自动化，不要求电脑在同一网络。</p></details>
    <p className="mobile-footnote">只给自己的设备配置。这里的文件夹只用于手机临时重试，电脑原件仍由 Eric OS 自动管理。</p></main>;
  return <main className="mobile-collector"><header className="mobile-heading"><h1>课堂笔记 → Eric OS</h1><p>首次配置后，在备忘录的“发送副本”里选择这条捷径。无需找文件、改名或传电脑。</p></header>
    {valid ? <><section className="personal-section"><h2>只配置一次分享捷径</h2><ol className="notes-setup-steps">
      <li>在“快捷指令”新建“Eric OS 课堂笔记”，打开“在共享表单中显示”，接收文件、图像和富文本。</li>
      <li>添加“制作 PDF”，使用共享表单传入的内容。已有 PDF 可直接传入。</li>
      <li>添加“获取 URL 内容”，地址与连接填写下方内容；方法选 POST，请求体选“表单”，增加文件字段 <code>file</code>，值为上一步的 PDF。</li>
      <li>最后检查返回内容中的 <code>saved</code>：为 true 时直接结束；否则用“显示提醒”显示返回的错误，保留原笔记后重试。</li>
    </ol><p>保存后，在备忘录点分享，确认选择“发送副本”，再选这条捷径。笔记里的 PDF 附件请用附件自己的分享按钮，以保留完整文件。电脑保存原件后才会返回 saved。成功时无需再点确认；离线或连接失效请保留设备上的笔记并重试。</p></section>
    <details className="personal-section desk-disclosure"><summary>捷径里填写的连接信息</summary><CopyValue label="接收地址" value={window.location.origin + '/api/notes'} /><p>在“获取 URL 内容”的“标头”中添加 <code>X-Eric-Notes-Key</code>，值填下面的专用连接。它只能上传笔记，不能读取或修改正式事项，也不是学习通登录信息。</p><CopyValue label="笔记连接" value={notesKey} secret /><p className="personal-meta">可选：表单增加 <code>shared_at</code>，使用当前日期并格式化为 yyyy-MM-dd，帮助识别当天课堂。</p></details>
    <p className="mobile-footnote">{persistent ? '已允许90天笔记专用连接。工作台可在之前选择的私人Wi-Fi自动接收；关闭收集、更换网络或连接到期后停止。' : '临时连接最长七天。停止手机收集、电脑重启或网络变化后需重新连接。'}请只在自己的同一 Wi-Fi 中使用，勿转发这页连接信息。</p></> : <section className="personal-section"><h2>请从电脑打开配置码</h2><p>在 Eric OS 的“手机收集”中生成笔记分享连接，再用这台设备扫码。本页没有收到有效连接。</p></section>}
  </main>;
}
