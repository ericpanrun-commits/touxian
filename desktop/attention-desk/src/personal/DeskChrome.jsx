import { useLayoutEffect, useState } from 'react';
import { Bell, BookOpen, Inbox, CalendarDays, CheckCheck, Globe2, ListTodo, MessageCircle, Monitor, Moon, Plus, Settings2, Sun, History, CircleCheck, Ellipsis } from 'lucide-react';

export default function DeskChrome({ mode, busy, onNavigate, onSwitchScreen, cloud = false }) {
  const [theme, setTheme] = useState(() => {
    try { const value = localStorage.getItem('attention-desk-theme'); return ['light', 'dark', 'system'].includes(value) ? value : 'system'; }
    catch { return 'system'; }
  });
  useLayoutEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const update = () => { document.documentElement.dataset.deskTheme = theme === 'system' ? media.matches ? 'dark' : 'light' : theme; };
    update(); media.addEventListener('change', update);
    try { localStorage.setItem('attention-desk-theme', theme); } catch { /* Appearance still works without persistent storage. */ }
    return () => media.removeEventListener('change', update);
  }, [theme]);
  const routes = cloud ? [['today','Today',Sun],['courses','课程',BookOpen],['inbox','Inbox',Inbox],['reviews','Review',CircleCheck],['recent','最近',History],['learning','学习',BookOpen],['tasks','事项',ListTodo]] : [['today', '今天', Sun], ['learning', 'Learning', BookOpen], ['eric-inbox', 'Inbox', Inbox], ['wechat-inbox', '待确认', Bell], ['list', '所有事项', ListTodo], ['calendar', '日历', CalendarDays]];
  const phoneRoutes = [['today','Today',Sun],['capture','Capture',Plus],['inbox','Inbox',Inbox],['reviews','Review',CircleCheck],['more','更多',Ellipsis]];
  const settings = /settings|user-context/.test(mode);
  const learningIntake = ['learning', 'school', 'eric-inbox'].includes(mode);
  return <><aside className={`desk-navigation${cloud?' cloud-navigation':''}`}><a className="desk-brand" href={cloud?'?view=today':'?personal_view=today'}><span><CheckCheck size={21} /></span>{cloud?'Eric OS':'Attention Desk'}</a>
    <nav className={cloud?'cloud-wide-navigation':undefined} aria-label="事项导航">{routes.map(([id, label, Icon]) => <button type="button" key={id} aria-current={mode === id || cloud&&(id==='courses'&&['course','lecture','textbooks'].includes(mode)||id==='reviews'&&mode==='review') ? 'page' : undefined} disabled={busy} onClick={() => onNavigate(id)}><Icon size={20} /><span>{label}</span></button>)}</nav>
    {cloud?<nav className="cloud-phone-navigation" aria-label="主要导航">{phoneRoutes.map(([id,label,Icon])=><button type="button" key={id} aria-current={mode===id||id==='reviews'&&mode==='review'||id==='more'&&mode!=='review'&&!phoneRoutes.some(route=>route[0]===mode)?'page':undefined} onClick={()=>onNavigate(id)}><Icon size={20}/><span>{label}</span></button>)}</nav>:null}
    <div className="desk-navigation-bottom">{cloud?null:<details><summary>其他来源</summary><button type="button" disabled={busy} onClick={() => onSwitchScreen?.('official')}><Globe2 size={18} />官网信息</button><button type="button" disabled={busy} onClick={() => onSwitchScreen?.('chat')}><MessageCircle size={18} />历史消息</button><button type="button" disabled={busy} onClick={() => onNavigate('collector')}>手机收集</button></details>}
      <button type="button" aria-current={cloud?mode==='system'?'page':undefined:settings?'page':undefined} disabled={busy} onClick={() => onNavigate(cloud?'system':'bark-settings')}><Settings2 size={20} /><span>{cloud?'设备与状态':'设置'}</span></button></div>
  </aside><header className="desk-toolbar"><span className="desk-toolbar-title">{cloud?'Eric OS':'Attention Desk'}</span><div className="desk-toolbar-actions"><div className="desk-theme" role="group" aria-label="外观">{[['light', '浅色', Sun], ['dark', '深色', Moon], ['system', '跟随系统', Monitor]].map(([value, label, Icon]) => <button type="button" key={value} title={label} aria-label={label} aria-pressed={theme === value} onClick={() => setTheme(value)}><Icon size={15} /></button>)}</div><button type="button" className="button button-primary" disabled={busy} aria-label={cloud?'新建 Capture':learningIntake?'Send to Eric OS':'收集通知'} onClick={() => cloud?onNavigate('capture'):learningIntake ? onNavigate('eric-inbox', null, { addInput: true }) : onNavigate('collect')}><Plus size={16} />{cloud?'Capture':learningIntake ? <><span className="desk-learning-desktop">Send to Eric OS</span><span className="desk-learning-mobile" aria-hidden="true">分享</span></> : '收集通知'}</button></div></header></>;
}
