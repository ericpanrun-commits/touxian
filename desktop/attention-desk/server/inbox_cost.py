"""A dated conservative estimate, never a provider invoice or a new caller."""
import json
from urllib.parse import urlsplit
from .personal_store import encode

SOURCE = 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'
PRICE_VERSION = '2026-10-27-peak-upper-bound'


def estimate(provider, model, usage, base_url, reused=False):
    result = {'provider': provider, 'model': model, 'token_usage': usage or {}, 'cloud_images': 0,
              'currency': 'CNY', 'price_version': PRICE_VERSION, 'source': SOURCE,
              'basis': '高峰价格保守估算；实际账单可能因空闲时段或缓存更低。', 'estimated_cost': None}
    if reused:
        return {**result, 'estimated_cost': 0, 'basis': '复用已有分析，本次未新增模型调用。'}
    usage = usage or {}
    if provider != 'deepseek' or urlsplit(base_url).hostname != 'api.deepseek.com' or not any(k in usage for k in ('prompt_tokens', 'completion_tokens')):
        return {**result, 'basis': '用量或受支持价格不足，费用未知；不能视为免费。'}
    name = model.casefold()
    if 'flash' in name: miss, hit, output = 2, .04, 8
    elif 'v4-pro' in name: miss, hit, output = 9, .3, 27
    else: return {**result, 'basis': '当前模型未匹配已核验价目，费用未知。'}
    prompt = max(0, usage.get('prompt_tokens', 0))
    cached = min(prompt, max(0, usage.get('prompt_cache_hit_tokens', 0)))
    completion = max(0, usage.get('completion_tokens', 0))
    return {**result, 'estimated_cost': round(((prompt-cached)*miss + cached*hit + completion*output)/1_000_000, 8),
            'rates_per_million': {'input': miss, 'cached_input': hit, 'output': output}}


def record_cost(c, extraction_id):
    if c.execute('SELECT COALESCE(max(version),0) FROM schema_migrations').fetchone()[0] < 21:
        return  # Historical-schema migration/recovery readers remain supported.
    row = c.execute('''SELECT a.*,i.inbox_kind FROM ai_extraction a JOIN raw_snapshot s ON s.id=a.raw_snapshot_id
        JOIN raw_item i ON i.id=s.raw_item_id WHERE a.id=?''', (extraction_id,)).fetchone()
    if not row or not row['inbox_kind']: return
    config = json.loads(row['config_json'] or '{}')
    cost = estimate(row['provider'], config.get('response_model') or row['model'], json.loads(row['token_usage_json'] or 'null'),
                    config.get('base_url', ''), bool(row['reused_from_id']))
    c.execute('UPDATE ai_extraction SET cost_json=? WHERE id=?', (encode(cost), extraction_id))
