import { useEffect, useRef, useState } from 'react';
import { BookOpen, CheckCheck, ChevronRight, CircleCheck, Ellipsis, GraduationCap, History, Home, Inbox, ListTodo, Monitor, Moon, PanelLeftClose, PanelLeftOpen, Plus, Search, Settings2, Sun, X } from 'lucide-react';
import DeskSheet from '../personal/DeskSheet';
import usePersonalResource from '../personal/usePersonalResource';

const routes = [['today', 'Today', Home], ['courses', '课程', BookOpen], ['learning', '学习', GraduationCap], ['tasks', '任务', ListTodo], ['inbox', 'Inbox', Inbox], ['reviews', 'Review', CircleCheck], ['recent', '最近', History]];
const phoneRoutes = [routes[0], routes[1], routes[2], routes[4], ['more', '更多', Ellipsis]];
const selected = (mode, id) => mode === id || id === 'courses' && ['course', 'lecture', 'textbooks'].includes(mode) || id === 'reviews' && mode === 'review';

export default function CloudChrome({ mode, busy, onNavigate, theme, setTheme }) {
  const [expanded, setExpanded] = useState(false), [search, setSearch] = useState(false), [query, setQuery] = useState('');
  const input = useRef(null), apple = /Mac|iPhone|iPad|iPod/.test(navigator.platform + navigator.userAgent);
  const courses = usePersonalResource('/courses?limit=100', { paused: !search });
  const navigate = (view, id) => { setSearch(false); setExpanded(false); onNavigate(view, id); };
  useEffect(() => {
    const key = event => {
      if (event.isComposing) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setSearch(value => !value); }
      if (event.key === 'Escape') setExpanded(false);
    };
    document.addEventListener('keydown', key); return () => document.removeEventListener('keydown', key);
  }, []);
  useEffect(() => { document.documentElement.dataset.sidebar = expanded ? 'expanded' : 'compact'; return () => { delete document.documentElement.dataset.sidebar; }; }, [expanded]);
  useEffect(() => { if (search) { const frame = requestAnimationFrame(() => input.current?.focus()); return () => cancelAnimationFrame(frame); } }, [search]);
  const pages = [...routes, ['capture', '收集笔记', Plus], ['textbooks', '教材', BookOpen], ['system', '设备与状态', Settings2]];
  const term = query.trim().toLocaleLowerCase('zh-CN'), matches = pages.filter(([, label]) => label.toLocaleLowerCase('zh-CN').includes(term));
  const courseMatches = (courses.data?.items || []).filter(course => course.name.toLocaleLowerCase('zh-CN').includes(term));
  return <>
    <aside className="desk-navigation cloud-navigation" aria-label="侧栏"><a className="desk-brand" href="?view=today" aria-label="Eric OS 首页"><span><CheckCheck size={21}/></span><strong>Eric OS</strong></a>
      <nav className="cloud-wide-navigation" aria-label="工作台导航">{routes.map(([id, label, Icon]) => <button type="button" key={id} title={label} aria-label={label} aria-current={selected(mode, id) ? 'page' : undefined} disabled={busy} onClick={() => navigate(id)}><Icon size={20}/><span>{label}</span></button>)}</nav>
      <div className="cloud-sidebar-note"><span>你的学习工作台</span><p>课程 · 笔记 · 每一天</p></div>
      <div className="desk-navigation-bottom"><div className="desk-theme" role="group" aria-label="外观">{[['light', '浅色', Sun], ['dark', '深色', Moon], ['system', '跟随系统', Monitor]].map(([value, label, Icon]) => <button type="button" key={value} title={label} aria-label={label} aria-pressed={theme === value} onClick={() => setTheme(value)}><Icon size={16}/></button>)}</div><button type="button" title="设备与状态" aria-label="设备与状态" aria-current={mode === 'system' ? 'page' : undefined} onClick={() => navigate('system')}><Settings2 size={20}/><span>设备与状态</span></button><div className="cloud-profile"><span>E</span><div><strong>Eric</strong><small>个人学习空间</small></div></div></div>
    </aside>
    <header className="desk-toolbar cloud-toolbar"><div className="cloud-toolbar-left"><button type="button" className="desk-icon-button cloud-sidebar-toggle" aria-label={expanded ? '收起侧栏' : '展开侧栏'} aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? <PanelLeftClose size={21}/> : <PanelLeftOpen size={21}/>}</button><span className="desk-toolbar-title">Eric OS</span><span className="cloud-breadcrumb">{routes.find(([id]) => selected(mode, id))?.[1] || ({ capture: '收集笔记', system: '设备与状态', more: '更多' })[mode] || '工作台'}</span></div>
      <div className="desk-toolbar-actions"><button type="button" className="cloud-search-button" aria-label="搜索课程与页面" aria-keyshortcuts="Control+k Meta+k" onClick={() => setSearch(true)}><Search size={17}/><span>搜索课程与页面</span><kbd>{apple ? '⌘ K' : 'Ctrl K'}</kbd></button><button type="button" className="button button-primary" aria-label="新建 Capture" disabled={busy} onClick={() => navigate('capture')}><Plus size={17}/><span>收集</span></button></div>
    </header>
    <nav className="cloud-bottom-tabs" aria-label="主要导航">{phoneRoutes.map(([id, label, Icon]) => <button type="button" key={id} aria-current={selected(mode, id) || id === 'more' && !phoneRoutes.some(([route]) => selected(mode, route)) ? 'page' : undefined} onClick={() => navigate(id)}><Icon size={21} strokeWidth={selected(mode, id) ? 2.4 : 1.7}/><span>{label}</span></button>)}</nav>
    {search ? <DeskSheet title="搜索课程与页面" onClose={() => setSearch(false)}><form className="eric-search" noValidate onSubmit={event => { event.preventDefault(); if (event.nativeEvent.isComposing) return; if (courseMatches[0] && term) navigate('course', courseMatches[0].id); else if (matches[0]) navigate(matches[0][0]); }}><Search size={19}/><input ref={input} type="search" autoComplete="off" value={query} placeholder="课程名称，或要打开的页面" aria-label="搜索课程与页面" onChange={event => setQuery(event.target.value)}/>{query ? <button type="button" className="desk-icon-button" aria-label="清除搜索" onClick={() => { setQuery(''); input.current?.focus(); }}><X size={18}/></button> : null}</form><p className="personal-meta" role="status">{term ? `${matches.length + courseMatches.length} 项结果` : '快速打开'}</p><ul className="cloud-link-list eric-search-results">{matches.map(([id, label, Icon]) => <li key={id}><button type="button" onClick={() => navigate(id)}><Icon size={18}/><span>{label}</span><ChevronRight size={16}/></button></li>)}{courseMatches.map(course => <li key={course.id}><button type="button" onClick={() => navigate('course', course.id)}><BookOpen size={18}/><span>{course.name}</span><ChevronRight size={16}/></button></li>)}</ul>{courses.error ? <p className="personal-field-error" role="alert">课程暂未读取。<button type="button" className="personal-text-button" onClick={courses.retry}>重新读取</button></p> : null}{term && !matches.length && !courseMatches.length ? <p className="personal-meta">没有找到匹配项，试试课程名的一部分。</p> : null}</DeskSheet> : null}
  </>;
}
