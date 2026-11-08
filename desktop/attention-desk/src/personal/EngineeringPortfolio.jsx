import { useEffect, useRef, useState } from 'react';
import { Box, Boxes, FileImage, FileText, FolderOpen, RefreshCw, Ruler, Search, X } from 'lucide-react';
import { personalRequest } from './api';
import { Field, SubmitButton } from './Forms';
import { preventImeSubmit } from './formHelpers';
import usePersonalResource from './usePersonalResource';
import DeskSheet from './DeskSheet';
import './portfolio.css';

const PAGE_SIZE = 24;
const FILE_ICONS = { SLDPRT: Box, SLDASM: Boxes, SLDDRW: Ruler, DWG: Ruler, DXF: Ruler, STEP: Box, STP: Box, PDF: FileText, JPG: FileImage, JPEG: FileImage, PNG: FileImage };
const number = new Intl.NumberFormat('zh-CN');
const modified = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short' });
const previewUrl = (project, file, version = '') => `/api/personal/portfolio/projects/${encodeURIComponent(project)}/preview/${encodeURIComponent(file)}${version ? '?v=' + encodeURIComponent(version) : ''}`;
function bytes(value) {
  if (value < 1024) return `${number.format(value)} B`;
  if (value < 1024 * 1024) return `${number.format(Math.round(value / 1024))} KB`;
  return `${number.format(Math.round(value / 1024 / 1024 * 10) / 10)} MB`;
}

function FileTypeIcon({ type, folder = false, size = 44 }) {
  const Icon = folder ? FolderOpen : FILE_ICONS[type] || FileText;
  return <Icon size={size} strokeWidth={1.3} aria-hidden="true" />;
}

function Cover({ project }) {
  const [failed, setFailed] = useState(false);
  const file = project.files.find(item => item.id === project.cover);
  const version = file ? `${file.modified_at}:${file.size}` : '';
  useEffect(() => setFailed(false), [project.cover, version]);
  return <span className="portfolio-cover">{project.cover && !failed
    ? <img src={previewUrl(project.id, project.cover, version)} alt="" loading="lazy" onError={() => setFailed(true)} />
    : <span className="portfolio-type-icon" role="img" aria-label={project.kind === 'file' ? `${project.types[0]} 文件图标` : '文件夹项目图标'}><FileTypeIcon type={project.types[0]} folder={project.kind !== 'file'} /><small>{project.kind === 'file' ? project.types[0] : '文件夹项目'}</small></span>}</span>;
}

export default function EngineeringPortfolio({ onDirtyChange, onBusyChange }) {
  const resource = usePersonalResource('/portfolio');
  const data = resource.data;
  const [query, setQuery] = useState(() => new URLSearchParams(location.search).get('portfolio_q') || '');
  const [type, setType] = useState(() => new URLSearchParams(location.search).get('portfolio_type') || 'all');
  const [visible, setVisible] = useState(PAGE_SIZE);
  const [selected, setSelected] = useState(null);
  const [preview, setPreview] = useState(null);
  const [settings, setSettings] = useState(false);
  const [root, setRoot] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const searchRef = useRef(null), rootRef = useRef(null), pending = useRef(false), alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { if (settings && error && !busy) rootRef.current?.focus(); }, [settings, error, busy]);
  useEffect(() => {
    const url = new URL(location.href);
    for (const [name, value] of [['portfolio_q', query], ['portfolio_type', type === 'all' ? '' : type]]) {
      if (value) url.searchParams.set(name, value); else url.searchParams.delete(name);
    }
    history.replaceState({}, '', url);
  }, [query, type]);
  useEffect(() => { setSelected(null); setPreview(null); setVisible(PAGE_SIZE); }, [data]);
  const term = query.trim().toLocaleLowerCase('zh-CN');
  const matches = (data?.items || []).filter(project => project.title.toLocaleLowerCase('zh-CN').includes(term) && (type === 'all' || project.types.includes(type)));
  const types = [...new Set((data?.items || []).flatMap(project => project.types))].sort();
  const selectType = value => { setType(value); setVisible(PAGE_SIZE); };
  const action = async operation => {
    if (pending.current) return;
    pending.current = true; setBusy(true); onBusyChange(true); setError(''); setNotice('');
    try { await operation(); }
    catch (value) { if (alive.current) setError(value.code === 'connection_error' ? '无法连接本地作品集，请确认本机工作台正在运行后重试。' : value.message); }
    finally { pending.current = false; if (alive.current) { setBusy(false); onBusyChange(false); } }
  };
  const saveRoot = event => {
    event.preventDefault();
    if (!root.trim()) { setError('请填写作品总目录的完整路径。'); rootRef.current?.focus(); return; }
    void action(async () => {
      await personalRequest('/portfolio/root', { method: 'PUT', body: { root: root.trim() } });
      if (!alive.current) return;
      onDirtyChange(false); setSettings(false); setNotice('作品目录已保存。'); resource.retry();
    });
  };
  const openFolder = project => void action(async () => {
    await personalRequest(`/portfolio/projects/${encodeURIComponent(project.id)}/open`, { method: 'POST' });
    if (alive.current) setNotice(project.kind === 'file' ? '已请求资源管理器打开所在文件夹。' : '已请求资源管理器打开作品文件夹。');
  });
  const refresh = () => { if (settings || busy) return; setError(''); setNotice(''); setSelected(null); setPreview(null); resource.retry(); };

  return <div className="engineering-portfolio">
    <header className="desk-page-heading portfolio-heading"><div><h1>工程作品集</h1><p>SolidWorks、AutoCAD 与手绘工程图，保留在你的文件夹里。</p></div><button type="button" className="button button-quiet" disabled={resource.loading || busy || settings} aria-busy={resource.loading} onClick={refresh}><RefreshCw size={16} className={resource.loading ? 'spin' : undefined} />刷新作品</button></header>
    <div className="portfolio-location"><FolderOpen size={17} aria-hidden="true" /><span>{data?.root || '本机作品目录'}</span><button type="button" className="personal-text-button" disabled={!data || data.managed || busy || settings} onClick={() => { setRoot(data.root); setSettings(true); setError(''); }}>修改目录</button></div>
    <p className="personal-meta portfolio-local-note">仅此电脑 · 文件和文件夹均可作为作品 · 新增或修改后，点击刷新作品。</p>
    {settings ? <form className="personal-section personal-form" noValidate onSubmit={saveRoot} onKeyDown={preventImeSubmit}>
      <Field name="portfolio-root" label="作品总目录" help="请先创建文件夹。目录必须位于 Git 仓库之外；修改路径不会移动原文件。" error={error || undefined}><input ref={rootRef} value={root} disabled={busy} onChange={event => { setRoot(event.target.value); setError(''); onDirtyChange(event.target.value !== data.root); }} /></Field>
      <div className="personal-form-actions"><SubmitButton busy={busy}>保存目录</SubmitButton><button type="button" className="button button-quiet" disabled={busy} onClick={() => { setSettings(false); setError(''); onDirtyChange(false); }}>取消修改</button></div>
    </form> : null}
    <div className="portfolio-feedback" aria-live="polite">{notice ? <p className="personal-feedback" role="status">{notice}</p> : null}{error && !settings ? <p className="personal-field-error" role="alert">{error}</p> : null}</div>
    {resource.error ? <p className="personal-field-error" role="alert">{resource.error.code === 'connection_error' ? '无法读取本机作品目录，请确认本地工作台正在运行。' : resource.error.message} <button type="button" className="personal-text-button" onClick={resource.retry}>重新读取作品</button></p> : null}
    {resource.loading ? <p className="personal-meta portfolio-read-status" role="status">正在读取作品文件夹…</p> : null}
    {data ? <>
      <div className="portfolio-tools"><div className="portfolio-search"><Search size={18} aria-hidden="true" /><input ref={searchRef} type="search" aria-label="搜索作品名称" placeholder="搜索作品名称" value={query} onChange={event => { setQuery(event.target.value); setVisible(PAGE_SIZE); }} />{query ? <button type="button" className="desk-icon-button" aria-label="清除作品搜索" onClick={() => { setQuery(''); setVisible(PAGE_SIZE); searchRef.current?.focus(); }}><X size={17} /></button> : null}</div>
        <Field name="portfolio-type" label="文件类型"><select value={type} onChange={event => selectType(event.target.value)}><option value="all">全部类型</option>{[...new Set([...types, ...(type === 'all' ? [] : [type])])].map(value => <option key={value} value={value}>{value}</option>)}</select></Field></div>
      <p className="personal-meta" role="status">{matches.length} 个作品{query || type !== 'all' ? ` · 共 ${data.items.length} 个` : ''}</p>
      {data.warnings.map(message => <p key={message} className="personal-meta" role="status">{message}</p>)}
      {!data.exists ? <section className="portfolio-empty"><FolderOpen size={32} /><h2>先创建作品总目录</h2><p>在资源管理器中创建 {data.root}，放入工程文件或项目文件夹，再点击刷新作品。</p><p>也可以通过「修改目录」选择已有的文件夹。</p></section>
        : !data.items.length ? <section className="portfolio-empty"><Box size={32} /><h2>把第一个作品放进来</h2><p>直接放入工程文件即可。多个关联文件可保留在同一个项目文件夹中，封面可选。</p></section>
          : !matches.length ? <section className="portfolio-empty"><Search size={32} /><h2>没有匹配的作品</h2><p>试试作品名称的一部分，或切换文件类型。</p><button type="button" className="button button-quiet" onClick={() => { setQuery(''); selectType('all'); searchRef.current?.focus(); }}>清除筛选</button></section>
            : <div className="portfolio-grid">{matches.slice(0, visible).map(project => <article key={project.id} className="portfolio-card"><button type="button" className="portfolio-card-open" aria-label={`查看作品：${project.title}`} onClick={() => { setSelected(project); setPreview(null); setError(''); setNotice(''); }}><Cover project={project} /><span className="portfolio-card-body"><strong>{project.title}</strong><span>{project.kind === 'file' ? `${project.types[0]} · 独立文件` : `文件夹项目 · ${project.files.length} 个支持文件${project.types.length ? ' · ' + project.types.join(' / ') : ''}`}</span>{project.warnings.length ? <small>部分内容未读取，查看详情</small> : null}</span></button></article>)}</div>}
      {visible < matches.length ? <button type="button" className="button button-quiet portfolio-load" onClick={() => setVisible(value => value + PAGE_SIZE)}>加载更多作品（已显示 {Math.min(visible, matches.length)} / {matches.length}）</button> : null}
      <p className="personal-meta portfolio-footnote">支持 JPG、JPEG、PNG、PDF、SLDPRT、SLDASM、SLDDRW、DWG、DXF、STEP、STP。原始工程文件不会上传或转换。</p>
    </> : null}
    {selected ? <DeskSheet wide title={selected.title} busy={busy} onClose={() => { setSelected(null); setPreview(null); setError(''); setNotice(''); }}>
      <div className="portfolio-detail-heading"><p className="personal-meta">{selected.kind === 'file' ? `${selected.types[0]} · 独立文件` : `${selected.files.length} 个支持文件 · 文件夹项目`} · 原文件保存在本机</p><button type="button" className="button button-primary" disabled={busy} onClick={() => openFolder(selected)}><FolderOpen size={16} />{selected.kind === 'file' ? '打开所在文件夹' : '打开作品文件夹'}</button></div>
      <div aria-live="polite">{notice ? <p className="personal-feedback" role="status">{notice}</p> : null}{error ? <p className="personal-field-error" role="alert">{error}</p> : null}</div>
      {selected.warnings.map(message => <p key={message} className="personal-meta">{message}</p>)}
      <p className="personal-meta">图片可在下方查看；PDF 使用浏览器本地预览。CAD / SolidWorks 文件请从作品文件夹使用对应软件打开。</p>
      <ul className="portfolio-files">{selected.files.map(file => <li key={file.id}><FileTypeIcon type={file.type} size={19} /><div><strong>{file.name}</strong><span>{file.type} · {bytes(file.size)} · {modified.format(new Date(file.modified_at))}</span></div>{file.preview === 'image' ? <button type="button" className="personal-text-button" onClick={() => setPreview(file)}>查看图片</button> : file.preview === 'pdf' ? <a href={previewUrl(selected.id, file.id)} target="_blank" rel="noreferrer">预览 PDF</a> : <span className="personal-meta">工程源文件</span>}</li>)}</ul>
      {!selected.files.length ? <p className="personal-meta">这个作品还没有支持的文件。添加后关闭详情，点击刷新作品。</p> : null}
      {preview ? <figure className="portfolio-image-preview"><figcaption>{preview.name}</figcaption><img key={preview.id} src={previewUrl(selected.id, preview.id)} alt={preview.name} onError={() => setError('图片暂时无法预览，可能已移动或格式有误。请关闭详情后刷新作品。')} /></figure> : null}
    </DeskSheet> : null}
  </div>;
}
