export const emptyEvent = (title = '') => ({ id: 'manual-' + crypto.randomUUID(), selected: true, title, summary: '', category: '其他', relevance: 'unclear', participation: 'later', notice_kind: 'new', audience: null, eligibility: [], deadlines: [], actions: [], materials: [], locations: [], uncertainties: [] });

export function reviewDraft(bundle) {
  if (bundle.draft?.events) return bundle.draft;
  const extracted = bundle.extraction?.parsed_output;
  if (!extracted || !extracted.events.length) return { acknowledged_warnings: false, events: [emptyEvent(bundle.title_hint)] };
  const spans = Object.fromEntries(extracted.evidence.map(span => [span.id, { quote: span.quote, start_offset: span.start, end_offset: span.end, ...(span.block_id ? { block_id: span.block_id } : {}) }]));
  const evidence = ids => ids.map(id => spans[id]).filter(Boolean).map(span => ({ ...span }));
  const fact = item => ({ text: item.text, evidence: evidence(item.evidence_ids) });
  return { acknowledged_warnings: false, events: extracted.events.map(event => ({ id: event.id, selected: true, title: event.title, summary: event.summary,
    category: event.category, relevance: 'unclear', participation: 'later', notice_kind: event.notice_kind, audience: event.audience ? fact(event.audience) : null,
    eligibility: event.eligibility.map(fact), materials: event.materials.map(fact), locations: event.locations.map(fact), uncertainties: event.uncertainties,
    deadlines: event.deadlines.map(deadline => ({ id: deadline.id, selected: deadline.relation === 'current', role: deadline.role, value: deadline.normalized_date || '',
      precision: deadline.precision === 'minute' ? 'minute' : 'date', original_text: deadline.original_text, evidence: evidence(deadline.evidence_ids), confirmed_by_user: false, relation: deadline.relation })),
    actions: event.actions.map(action => ({ id: action.id, selected: true, title: action.title, kind: action.kind, evidence: evidence(action.evidence_ids),
      deadline_id: event.deadlines.find(deadline => deadline.relation === 'current' && deadline.related_action_ids.includes(action.id))?.id || null })) })) };
}
