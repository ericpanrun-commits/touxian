export const reviewHasContent = review => review?.has_content === true || review?.has_content === 1;
export const reviewHasNoContent = review => review?.has_content === false || review?.has_content === 0;

export function preferredLectureReview(lecture, requestedId) {
  if (requestedId) return requestedId;
  const versions = lecture?.artifacts || [];
  const adopted = versions.find(item => item.id === lecture?.accepted_artifact_id);
  return (adopted && !reviewHasNoContent(adopted) ? adopted.id : null)
    || versions.find(reviewHasContent)?.id || versions[0]?.id || null;
}

export function reviewStateLabel(review, labels) {
  return reviewHasNoContent(review) && !['processing', 'waiting', 'running'].includes(review.status)
    ? '未完成' : labels[review.status] || '待核对';
}

export function noteFormat(document) {
  const mime = (document.mime_type || '').toLowerCase();
  const extension = (document.extension || '').toLowerCase();
  if (mime.startsWith('image/') || ['png', 'jpg', 'jpeg', 'webp'].includes(extension)) return 'image';
  if (mime === 'application/pdf' || extension === 'pdf') return 'pdf';
  return 'document';
}
export function reviewIssueMessage(item) {
  if(item.code==='schema')return '这部分内容不完整，需要核对后补全。';
  return String(item.message||'这段内容仍需对照来源核对。')
    .replace('Textbook 事实','教材原文引用')
    .replace('数学表达、变量、趋向或数值须用事实占位，不能自行重抄。','请通过来源原文核对数学表达，保留原引用并改写周围说明。');
}
