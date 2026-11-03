import { inboxCard } from './inboxCard.js';

function modelOf(item) {
  if (item?.card?.body) return {
    body: item.card.body,
    notice: item.card.notice,
    title: item.card.title,
    course: item.card.course,
    summary: item.card.summary,
    readable: item.card.readable_content,
    visionStatus: item.card.vision_status,
    keyPointCount: item.card.key_point_count,
    equationCount: item.card.equation_count,
    filename: item.card.filename,
  };
  return inboxCard(item);
}

export function EricInbox({ items = [], selectedId, advanced = false, onSelect }) {
  const selected = items.find((item) => item.id === selectedId) || null;
  const selectedModel = selected ? modelOf(selected) : null;
  return (
    <div className="eric-inbox">
      <ul>
        {items.map((item) => {
          const model = modelOf(item);
          return (
            <li key={item.id}>
              <button type="button" onClick={() => onSelect?.(item.id)}>
                <strong>{model.title || item.title || '未命名'}</strong>
                {model.course ? <small>{model.course}</small> : null}
                <p>{model.body}</p>
                {model.visionStatus ? <small>{model.visionStatus}</small> : null}
              </button>
            </li>
          );
        })}
      </ul>
      {selectedModel ? (
        <article>
          <header>
            <h2>{selectedModel.title}</h2>
            <p>{[selectedModel.course, selected?.created_at, selectedModel.filename].filter(Boolean).join(' · ')}</p>
            {selectedModel.visionStatus ? <p>视觉状态：{selectedModel.visionStatus}</p> : null}
            <p>{selectedModel.keyPointCount} 个要点 · {selectedModel.equationCount} 条公式</p>
          </header>
          <section>
            <h3>提取</h3>
            {selectedModel.notice ? <p>{selectedModel.notice}</p> : null}
            {selectedModel.summary ? <p>{selectedModel.summary}</p> : null}
            {selectedModel.readable ? <p>{selectedModel.readable}</p> : null}
            {!selectedModel.summary && !selectedModel.readable ? <p>{selectedModel.body}</p> : null}
          </section>
          {advanced ? (
            <details>
              <summary>调试 / OCR</summary>
              <pre>{selected?.debug?.ocr_preview || selected?.ocr_preview || selected?.review?.snapshot?.original_text || ''}</pre>
            </details>
          ) : null}
        </article>
      ) : null}
    </div>
  );
}
