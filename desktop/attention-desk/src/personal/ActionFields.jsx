import { Field } from './Forms';

export function KeepAction({ action, onChange }) {
  return <label className="personal-checkbox-label"><input type="checkbox" checked={action.selected} onChange={event => onChange(event.target.checked)} />保留行动：{action.title || '新行动'}</label>;
}

export function ActionDeadlineField({ action, deadlines, name, error, onChange }) {
  return <Field label="关联截止时间" name={name} error={error}><select value={action.deadline_id || ''} onChange={event => onChange(event.target.value || null)}><option value="">不关联 · 未设截止日期</option>{deadlines.filter(date => date.selected).map(date => <option key={date.id} value={date.id}>{date.role} · {date.value?.replace('T', ' ') || '日期待确认'}</option>)}</select></Field>;
}
