// A user-invoked bookmark reads visible text only. It cannot answer or submit.
export function learningBookmarklet(base = 'http://127.0.0.1:8765/') {
  const destination = new URL(base); destination.searchParams.set('personal_view', 'learning');
  const script = `(()=>{const parts=[document.body?.innerText||''];for(const frame of document.querySelectorAll('iframe')){try{const text=frame.contentDocument?.body?.innerText;if(text)parts.push(text)}catch{}}const selected=window.getSelection()?.toString().trim();const body=selected||parts.join('\\n');const text='学习通页面记录\\n'+document.title+'\\n'+location.origin+location.pathname+'\\n'+body;const payload=text.length>78000?{error:'页面太长，请先选中本次结果文字，再点击书签。'}:{title:document.title.slice(0,200),text};window.open(${JSON.stringify(destination.href)}+'#learning-share='+encodeURIComponent(JSON.stringify(payload)),'_blank','noopener,noreferrer')})()`;
  return 'javascript:' + script;
}

export function consumeLearningShare(locationValue = window.location) {
  if (!locationValue.hash.startsWith('#learning-share=')) return null;
  let payload;
  try {
    payload = JSON.parse(decodeURIComponent(locationValue.hash.slice('#learning-share='.length)));
    if (payload.error) payload = { error: String(payload.error).slice(0,300) };
    else if (typeof payload.text !== 'string' || payload.text.length > 80000 || payload.text.length < 2) payload = { error: '分享内容无法读取，请回到结果页重新分享。' };
    else payload = { text: payload.text, title: typeof payload.title === 'string' ? payload.title.slice(0,200) : '学习通学习记录', request_id: crypto.randomUUID() };
  } catch { payload = { error: '分享内容无法读取，请回到结果页重新分享。' }; }
  const clean = new URL(locationValue.href); clean.hash = '';
  history.replaceState({}, '', clean);
  return payload;
}
