export const emptyEvent = (title = '') => ({ id: 'manual-' + crypto.randomUUID(), selected: true, course_id: null, title, summary: '', category: '其他', relevance: 'unclear', participation: 'later', notice_kind: 'new', audience: null, eligibility: [], deadlines: [], actions: [], materials: [], locations: [], uncertainties: [] });

export function editReviewDate(event, id, fields) {
  const deadlines = event.deadlines.map(date => date.id === id ? { ...date, ...fields } : date);
  const selected = new Set(deadlines.filter(date => date.selected).map(date => date.id));
  const affected = event.actions.filter(action => action.deadline_id && !selected.has(action.deadline_id));
  return { event: { ...event, deadlines, actions: event.actions.map(action => affected.includes(action) ? { ...action, deadline_id: null } : action) }, affected };
}

export function confirmationSummary(event) {
  const dates = event.deadlines.filter(date => date.selected).length;
  const actions = event.actions.filter(action => action.selected).length;
  const execute = event.participation === 'yes' && event.relevance !== 'no' && event.notice_kind !== 'cancellation';
  return `将保存 1 个事项、${dates} 个日期；${execute ? `创建 ${actions} 个待办任务` : `不创建任务，保留 ${actions} 个行动方案`}。`;
}

export function reviewDraft(bundle, candidateId = null) {
  const active = bundle.wechat_candidates?.filter(item => item.status === 'needs_review');
  const focused = candidateId ? active?.find(item => item.id === candidateId) : null;
  const filter = values => ({ ...values, events: values.events.filter(event => {
    if (!active) return true;
    if (candidateId && !focused) return false;
    if (focused && focused.model_item_id !== '_pending') return event.id === focused.model_item_id;
    return active.some(item => item.model_item_id === '_pending' || item.model_item_id === event.id);
  }) });
  if (bundle.draft?.events) {
    const saved = filter(structuredClone(bundle.draft));
    if (saved.events.length) return saved;
  }
  const extracted = bundle.extraction?.parsed_output;
  if (!extracted || !extracted.events.length) return filter({ acknowledged_warnings: false, events: [{ ...emptyEvent(bundle.title_hint), course_id: bundle.item?.inbox_course_id || null }] });
  const spans = Object.fromEntries(extracted.evidence.map(span => [span.id, { quote: span.quote, start_offset: span.start, end_offset: span.end, ...(span.block_id ? { block_id: span.block_id } : {}) }]));
  const evidence = ids => ids.map(id => spans[id]).filter(Boolean).map(span => ({ ...span }));
  const fact = item => ({ text: item.text, evidence: evidence(item.evidence_ids) });
  return filter({ acknowledged_warnings: false, events: extracted.events.map(event => ({ id: event.id, selected: true, course_id: bundle.item?.inbox_course_id || null, title: event.title, summary: event.summary,
    category: event.category, relevance: 'unclear', participation: 'later', notice_kind: event.notice_kind, audience: event.audience ? fact(event.audience) : null,
    eligibility: event.eligibility.map(fact), materials: event.materials.map(fact), locations: event.locations.map(fact), uncertainties: event.uncertainties,
    deadlines: event.deadlines.map(deadline => ({ id: deadline.id, selected: deadline.relation === 'current' && Boolean(deadline.normalized_date) && !deadline.uncertain, role: deadline.role, value: deadline.normalized_date || '',
      precision: deadline.precision, end_value: deadline.end_value || null, range_precision: deadline.range_precision || null, original_text: deadline.original_text, evidence: evidence(deadline.evidence_ids), confirmed_by_user: false, relation: deadline.relation })),
    actions: event.actions.map(action => ({ id: action.id, selected: true, title: action.title, kind: action.kind, evidence: evidence(action.evidence_ids),
      deadline_id: event.deadlines.find(deadline => deadline.relation === 'current' && deadline.related_action_ids.includes(action.id))?.id || null })) })) });
}

export function wechatConfirmation(bundle, candidate) {
  const draft = reviewDraft(bundle, candidate.id);
  const item = candidate.analysis;
  const warnings = bundle.extraction?.validation?.warnings || [];
  const requiresReview = !draft.events.length || item.analysis_origin === 'local_pending' || item.relevant !== true
    || Boolean(bundle.draft?.events?.some(event => event.id === candidate.model_item_id))
    || (item.action_required && item.participation !== 'required')
    || warnings.some(warning => warning.code !== 'json_repaired_locally')
    || draft.events.some(event => event.deadlines.some(date => date.relation === 'current' && !date.selected));
  if (requiresReview) return null;
  return { ...draft, expected_version: bundle.version, extraction_id: bundle.extraction_id, acknowledged_warnings: true,
    events: draft.events.map(event => ({ ...event, selected: true, relevance: 'yes',
      participation: item.action_required ? 'yes' : 'later',
      deadlines: event.deadlines.map(date => ({ ...date, confirmed_by_user: date.selected })) })) };
}
