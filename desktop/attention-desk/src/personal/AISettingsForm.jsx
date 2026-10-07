import { Field, SubmitButton } from './Forms';
import { preventImeSubmit, useDraft } from './formHelpers';

export default function AISettingsForm({ configuration, busy, onSave, onCancel, onDirtyChange }) {
  const draft = useDraft({ base_url: configuration.base_url, model: configuration.model, timeout: String(configuration.timeout),
    max_output_tokens: String(configuration.max_output_tokens), thinking: configuration.thinking, api_key: '', clear_api_key: false });
  const submit = (event) => {
    event.preventDefault();
    if (busy) return;
    const next = {};
    try { const url = new URL(draft.values.base_url); if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) throw Error(); }
    catch { next.base_url = '请填写不含密钥或查询参数的 HTTPS 地址。'; }
    if (!draft.values.model.trim() || draft.values.model.length > 100) next.model = '请填写模型名。';
    if (!/^\d+$/.test(draft.values.timeout) || Number(draft.values.timeout) < 5 || Number(draft.values.timeout) > 180) next.timeout = '超时应为 5—180 秒。';
    if (!/^\d+$/.test(draft.values.max_output_tokens) || Number(draft.values.max_output_tokens) < 256 || Number(draft.values.max_output_tokens) > 32768) next.max_output_tokens = '输出上限应为 256—32768 tokens。';
    if (draft.validate(next)) onSave({ ...draft.values, timeout: Number(draft.values.timeout), max_output_tokens: Number(draft.values.max_output_tokens) }, () => draft.field('api_key', ''));
  };
  return <form ref={draft.formRef} className="personal-form" noValidate onSubmit={submit} onKeyDown={preventImeSubmit} onChange={() => onDirtyChange(true)}>
    <fieldset disabled={busy}>
      <Field label="服务地址" name="base_url" help="默认 https://api.deepseek.com；可填写 base URL 或 chat/completions 地址。" error={draft.errors.base_url}><input value={draft.values.base_url} onChange={event => draft.field('base_url', event.target.value)} /></Field>
      <Field label="模型" name="model" error={draft.errors.model}><input value={draft.values.model} onChange={event => draft.field('model', event.target.value)} /></Field>
      <Field label="DeepSeek API Key" name="api_key" help={configuration.has_api_key ? '已有密钥。留空保留；新密钥仅发送到本机后端，由当前 Windows 用户加密。' : '填写后由当前 Windows 用户加密保存。不会放入 localStorage 或返回到页面。'}><input type="password" autoComplete="new-password" spellCheck={false} value={draft.values.api_key} onChange={event => draft.field('api_key', event.target.value)} /></Field>
      <div className="personal-form-grid">
        <Field label="超时（秒）" name="timeout" error={draft.errors.timeout}><input inputMode="numeric" value={draft.values.timeout} onChange={event => draft.field('timeout', event.target.value)} /></Field>
        <Field label="输出 token 上限" name="max_output_tokens" error={draft.errors.max_output_tokens}><input inputMode="numeric" value={draft.values.max_output_tokens} onChange={event => draft.field('max_output_tokens', event.target.value)} /></Field>
      </div>
      <label className="personal-checkbox-label"><input type="checkbox" checked={draft.values.thinking} onChange={event => draft.field('thinking', event.target.checked)} />开启 thinking（普通通知默认关闭）</label>
      {configuration.key_source === 'windows' ? <label className="personal-checkbox-label"><input type="checkbox" checked={draft.values.clear_api_key} onChange={event => draft.field('clear_api_key', event.target.checked)} />清除已保存密钥</label> : null}
      <p className="personal-meta">{configuration.has_api_key ? '密钥已配置' : '密钥未配置，仍可保存原文和手动整理'} · provider：DeepSeek</p>
    </fieldset>
    <div className="personal-form-actions"><SubmitButton busy={busy}>保存提取设置</SubmitButton><button type="button" className="button button-quiet" disabled={busy} onClick={onCancel}>返回工作台</button></div>
  </form>;
}
