import { FolderOpen } from 'lucide-react';
import '../personal/portfolio.css';

// Deliberately contains no file API, cloud transport, cache, upload or sync calls.
export default function LocalPortfolioNotice() {
  return <section className="personal-section"><FolderOpen size={30} aria-hidden="true" /><h2>工程作品保存在 Windows 本机</h2>
    <p>当前 Web / PWA 无法直接读取 D 盘目录。请在存放作品的 Windows 电脑上，打开本地工作台的「工程作品集」。</p>
    <p className="personal-meta">默认目录：D:\Engineering-Portfolio。作品不会上传到云端，手机、iPad 和其他电脑无法查看这些本地文件。</p>
    <a className="button button-primary portfolio-local-launch" href="http://127.0.0.1:8765/?personal_view=portfolio" target="_blank" rel="noreferrer"><FolderOpen size={16} />打开本机作品集</a>
    <p className="personal-meta">请先在 Windows 打开本地工作台。若使用自定义端口，请直接从本地工作台侧栏进入工程作品集。</p>
  </section>;
}
