"""Shared DeepSeek HTTP boundary. No domain writes; optional per-attempt gate."""
from __future__ import annotations

import asyncio
import json

import httpx

from .extraction_errors import ExtractionFailure
from .recovery_controls import paid_ai_paused


def strict_json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
    except (ValueError, TypeError):
        raise ExtractionFailure('invalid_json','模型未返回完整、严格的 JSON；原文保留。',raw_output=raw) from None


def safe_usage(value):
    if not isinstance(value, dict):
        return {}
    return {k:value[k] for k in ('prompt_tokens','completion_tokens','total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens')
            if isinstance(value.get(k),int) and not isinstance(value[k],bool) and value[k]>=0}


class DeepSeekTransport:
    def __init__(self, transport=None):
        self.transport = transport

    async def request(self, body, config, api_key, *, attempts=1, before_attempt=None):
        if paid_ai_paused():
            raise ExtractionFailure('paid_analysis_paused','付费分析保持暂停；原文与待处理任务保留。',attempts=0)
        from .extraction_provider import ProviderResult
        key = api_key.get_secret_value()
        if not key:
            raise ExtractionFailure('missing_key','原文已保存，请配置 DeepSeek 密钥。',attempts=0)
        endpoint = config.base_url.rstrip('/')
        if not endpoint.endswith('/chat/completions'):
            endpoint += '/chat/completions'
        errors = {401:('unauthorized','DeepSeek 密钥无效。'),403:('forbidden','DeepSeek 拒绝访问。'),
                  402:('insufficient_balance','DeepSeek 余额不足。'),429:('rate_limited','DeepSeek 请求受限，请稍后重试。')}
        count = 0
        try:
            async with httpx.AsyncClient(timeout=config.timeout,transport=self.transport,follow_redirects=False) as client:
                for index in range(max(1,min(2,attempts))):
                    if paid_ai_paused():
                        raise ExtractionFailure('paid_analysis_paused','付费分析保持暂停。',attempts=count)
                    if before_attempt:
                        before_attempt()
                    count += 1
                    response = await client.post(endpoint,json=body,headers={'Authorization':'Bearer '+key})
                    if response.status_code in {429,502,503} and index+1<attempts:
                        await asyncio.sleep(0.5)
                        continue
                    if response.status_code>=400 or response.is_redirect:
                        code,message=errors.get(response.status_code,('provider_error','模型服务暂时不可用，原文保留。'))
                        try:
                            retry_after=max(0,min(86400,float(response.headers.get('Retry-After','300'))))
                        except ValueError:
                            from email.utils import parsedate_to_datetime
                            from datetime import datetime,timezone
                            try:
                                retry_after=max(0,min(86400,(parsedate_to_datetime(response.headers['Retry-After'])-datetime.now(timezone.utc)).total_seconds()))
                            except (ValueError,KeyError,TypeError):
                                retry_after=300
                        raise ExtractionFailure(code,message,attempts=count,retryable=response.status_code in {429,502,503},retry_after=retry_after)
                    if len(response.content)>2_000_000:
                        raise ExtractionFailure('response_too_large','模型返回过大。',attempts=count)
                    try:
                        data=response.json()
                        choice=data['choices'][0]
                        content=choice['message']['content']
                        usage=safe_usage(data.get('usage'))
                    except (ValueError,KeyError,IndexError,TypeError):
                        raise ExtractionFailure('invalid_response','模型响应结构无效。',attempts=count) from None
                    raw=content.replace(key,'[redacted]') if isinstance(content,str) else ''
                    reason=choice.get('finish_reason')
                    if reason=='length' or usage.get('completion_tokens',0)>=config.max_output_tokens:
                        raise ExtractionFailure('truncated','模型输出达到上限，未使用不完整候选。',raw_output=raw,usage=usage,attempts=count,finish_reason=reason)
                    if reason!='stop':
                        raise ExtractionFailure('incomplete_output','模型输出未正常完成。',raw_output=raw,usage=usage,attempts=count,finish_reason=reason)
                    if not raw.strip():
                        raise ExtractionFailure('empty_output','模型返回为空，原文保留。',raw_output=raw,usage=usage,attempts=count)
                    model=data.get('model','')
                    return ProviderResult(raw,usage,model[:100].replace(key,'[redacted]') if isinstance(model,str) else '',reason,count)
        except httpx.TimeoutException:
            raise ExtractionFailure('timeout','分析超时；结果未知，不自动重复请求。',attempts=count) from None
        except httpx.HTTPError:
            raise ExtractionFailure('connection_error','请求中断；结果未知，原文保留。',attempts=count) from None
