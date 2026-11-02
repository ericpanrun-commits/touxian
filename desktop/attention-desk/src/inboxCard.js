export const WAITING_COPY = '等待 AI 分析…';
export const FAILED_COPY = 'AI 分析失败，可重新处理';
export const LOW_COPY = '部分内容需要确认';

const IMAGE_EXT = new Set(['png', 'jpg', 'jpeg', 'webp', 'heic', 'heif', 'pdf']);

export function isImageOrPdf(item) {
  if (item?.input_kind === 'image' || item?.input_kind === 'pdf') return true;
  const files = [...(item?.attachments || []), ...(item?.files || [])];
  return files.some((file) => {
    const mime = file?.mime_type || file?.mime || '';
    return mime.startsWith('image/') || mime === 'application/pdf' || IMAGE_EXT.has(String(file?.extension || '').toLowerCase());
  });
}

/** Card body for an inbox payload. Unverified OCR in preview/original_text is not the body. */
export function inboxCard(item) {
  const analysis = item?.vision_analysis && typeof item.vision_analysis === 'object' && !Array.isArray(item.vision_analysis) ? item.vision_analysis : {};
  const status = item?.vision_status || null;
  const low = item?.vision_confidence === 'low' || item?.preview_source === 'low_confidence' || analysis.confidence === 'low';
  const file = (item?.attachments || [])[0] || {};
  let bodySource = 'text';
  let body = typeof item?.preview === 'string' ? item.preview : '';
  let notice = null;
  if (item?.preview_source === 'human' && body.trim()) bodySource = 'human';
  else if (status === 'failed' || item?.preview_source === 'failed') {
    bodySource = 'failed';
    body = FAILED_COPY;
    notice = FAILED_COPY;
  } else if ((status === 'completed' || item?.preview_source === 'vision' || item?.preview_source === 'low_confidence') && low) {
    bodySource = 'low_confidence';
    body = LOW_COPY;
    notice = LOW_COPY;
  } else if (status === 'completed' || item?.preview_source === 'vision') {
    bodySource = 'vision';
    body = analysis.summary || analysis.readable_content || body || WAITING_COPY;
  } else if (isImageOrPdf(item)) {
    bodySource = 'waiting';
    body = WAITING_COPY;
    notice = WAITING_COPY;
  }
  return {
    bodySource,
    body,
    notice,
    title: analysis.title || item?.title || file.filename || '',
    course: item?.course?.name || item?.course?.label || analysis.probable_course || null,
    summary: analysis.summary || null,
    readable: analysis.readable_content || null,
    visionStatus: status,
    confidence: item?.vision_confidence || analysis.confidence || null,
    keyPointCount: Array.isArray(analysis.key_points) ? analysis.key_points.length : 0,
    equationCount: Array.isArray(analysis.equations) ? analysis.equations.length : 0,
    filename: file.filename || null,
    mime: file.mime_type || file.mime || null,
  };
}
