import { Paperclip } from 'lucide-react';
import usePersonalResource from './usePersonalResource';

export default function WeChatMediaEntry({ onOpen }) {
  const resource = usePersonalResource('/wechat/media', { refreshInterval: 15000 });
  const data = resource.data;
  if (!data?.pending) return null;
  return <button type="button" className="personal-text-button desk-media-entry" onClick={() => {
    const url = new URL(window.location.href); url.searchParams.set('wechat_media', '1'); window.history.replaceState({}, '', url); onOpen();
  }}><Paperclip size={15} />{data.pending} 份附件{data.processing ? '正在识别' : '待解析'}<span>{data.quiet ? '06:00 后自动继续' : '查看原件与处理状态'}</span></button>;
}
