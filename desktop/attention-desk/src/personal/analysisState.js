export function analysisProgress(extraction) {
  const p = extraction?.progress;
  if (!p) return null;
  const labels = { planning: '发现文本区域', analyzing: '分析区域', merging: '合并结果', review: '等待确认', cancelled: '分析已取消', failed: '分析未完成', truncated: '输出未完整结束' };
  return { label: labels[p.phase] || '分析中', completed: Math.min(p.processed || 0, p.total || 0), total: p.total || 0,
    cached: p.cached || 0, calls: p.calls || 0, incomplete: (p.failed_regions || 0) + (p.omitted_regions || 0), state: extraction.analysis_state };
}

export function analysisRequestBody(force, confirmBudget, scope) {
  return { force, confirm_budget: confirmBudget, scope };
}
