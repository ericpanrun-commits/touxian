import { EvidenceLinks } from './Evidence';

export default function EventContext({ event, busy, terminal, onParticipation, onView }) {
  const context = event.context;
  if (!context) return null;
  return <section className="personal-section personal-event-context"><h2>我的决定与提取来源</h2>
    <p className="personal-meta">相关性：{{ yes: '与我相关', no: '与我无关', unclear: '尚未确定' }[context.relevance]} · 参与：{{ yes: '参加 / 执行', no: '不参加', later: '稍后决定' }[context.participation]}</p>
    {context.participation !== 'yes' && context.relevance !== 'no' && !context.tasks_activated && context.notice_kind !== 'cancellation' ? <div className="personal-event-actions"><button type="button" className="button button-primary" disabled={busy || terminal} onClick={() => onParticipation('yes')}>参加并创建任务</button><button type="button" className="button button-quiet" disabled={busy || terminal} onClick={() => onParticipation('no')}>不参加</button></div> : null}
    {context.planned_actions.length ? <div><h3>尚未创建为任务的行动方案</h3><ul>{context.planned_actions.map(action => <li key={action.id}>{action.title} · {{ explicit: '原文明确要求', suggested: '辅助建议', manual: '我自行添加' }[action.kind]}<EvidenceLinks evidence={event.evidence} fieldPath={`planned_actions.${action.id}.title`} onView={onView} /></li>)}</ul></div> : null}
    {['audience', 'eligibility', 'materials', 'locations'].map(group => {
      const facts = group === 'audience' ? context.audience ? [context.audience] : [] : context[group];
      return facts.length ? <div key={group}><h3>{{ audience: '受众', eligibility: '资格条件', materials: '所需材料（原文提及）', locations: '地点' }[group]}</h3><ul>{facts.map((fact, index) => <li key={index}>{fact.text}<EvidenceLinks evidence={event.evidence} fieldPath={`${group}.${index}.text`} onView={onView} /></li>)}</ul></div> : null;
    })}
    {context.uncertainties.length ? <div><h3>核对记录</h3><ul>{context.uncertainties.map((text, index) => <li key={index}>{text}</li>)}</ul></div> : null}
    <p className="personal-meta">{context.extraction ? `来源：${context.extraction.provider} / ${context.extraction.model} · ${context.extraction.prompt_version} · schema ${context.extraction.schema_version} · thinking ${context.extraction.thinking ? '开' : '关'}` : '手动整理并确认'}</p>
    <p className="personal-meta">{event.priority?.level ? `本地优先级：${event.priority.level} · ` : ''}{event.priority?.reason}</p>
  </section>;
}
