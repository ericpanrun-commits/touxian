import { useRef, useState } from 'react';

export function preventImeSubmit(event) {
  if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.keyCode === 229)) event.preventDefault();
}

export function validTime(value, precision) {
  const text = value.trim().replace(' ', 'T');
  const match = precision === 'date' ? /^(\d{4})-(\d{2})-(\d{2})$/.exec(text) : /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(text);
  if (!match || Number(match[1]) === 0) return false;
  const parsed = new Date(precision === 'date' ? text + 'T00:00:00Z' : text + ':00Z');
  return parsed.getUTCFullYear() === Number(match[1]) && parsed.getUTCMonth() + 1 === Number(match[2]) && parsed.getUTCDate() === Number(match[3])
    && (precision === 'date' || (parsed.getUTCHours() === Number(match[4]) && parsed.getUTCMinutes() === Number(match[5])));
}

export function useDraft(initial) {
  const [values, setValues] = useState(initial);
  const [errors, setErrors] = useState({});
  const formRef = useRef(null);
  const field = (name, value) => { setValues(current => ({ ...current, [name]: value })); setErrors(current => ({ ...current, [name]: undefined })); };
  const validate = (next) => { setErrors(next); const first = Object.keys(next)[0]; if (first) formRef.current?.elements.namedItem(first)?.focus(); return !first; };
  const clearError = name => setErrors(current => ({ ...current, [name]: undefined }));
  return { values, setValues, errors, formRef, field, validate, clearError };
}

export function quoteSpan(original, quote) {
  const utf16 = original.indexOf(quote);
  const start = utf16 >= 0 ? Array.from(original.slice(0, utf16)).length : 0;
  return { quote, start_offset: start, end_offset: start + Array.from(quote).length };
}

export function exactSpan(original, span) {
  return Number.isInteger(span.start_offset) && Number.isInteger(span.end_offset) && span.start_offset >= 0 && span.end_offset > span.start_offset
    && span.end_offset <= Array.from(original).length
    && Array.from(original).slice(span.start_offset, span.end_offset).join('') === span.quote;
}
