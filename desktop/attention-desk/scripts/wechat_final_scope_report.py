"""Generate a local Raw/field/access review and an unapproved upload request draft."""
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from wechat_backlog_plan import ReadOnlyStore
from server.personal_store import default_database_path
from server.cloud_sync.minimized_projection import minimized_projection_preview


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def civil(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S') if value else '未设置'


def paths(value,prefix=''):
    if isinstance(value,dict):
        for key,child in value.items():
            name=prefix+'.'+key if prefix else key
            yield name
            yield from paths(child,name)
    elif isinstance(value,list):
        for child in value[:1]:yield from paths(child,prefix+'[]')


def main():
    folder=ROOT/'artifacts/p0-20261104';evidence=folder/'final-scope-review'
    samples=read(folder/'wechat-minimized-text-samples.json');bundle=read(folder/'wechat-minimized-projection-preview.json')
    regenerated=minimized_projection_preview(ReadOnlyStore(default_database_path()),samples)
    assert regenerated==bundle, 'reviewed bundle changed'
    acl=read(evidence/'acl-evidence.json');access=read(evidence/'confirmed-access-review.json')
    live=read(evidence/'live-read-access-checks.json');presence=read(evidence/'record-presence-review.json')
    assert live['PASS'] and access['user_confirmed_all_sessions'] and presence['existing_reviewed_records'] is None
    assert all(d['owner_matches'] and d['auth_session_exists'] and d['session_user_matches'] and not d['user_deleted'] and not d['user_banned'] for d in access['devices'])
    assert all(t['relrowsecurity'] for t in acl['rls'])
    assert not any(r['can_select'] or r['can_insert'] or r['can_update'] or r['can_delete'] for r in acl['direct_access'] if r['rolname'] in ('anon','authenticated'))
    assert not any(r['anon_execute'] or r['authenticated_execute'] for r in acl['rpcs'])
    assert len(bundle['records'])==8 and not bundle['resources'] and bundle['upload_authorized'] is False
    contract=read(folder/'minimized-read-model-contract.json')
    publish_contract=read(evidence/'minimized-publish-contract.json')
    browser=read(ROOT.parents[2]/'p0-notification-evidence-20261104/inbox-minimized-preview.json')
    for result in (contract,publish_contract,browser):
        assert result['PASS'] and result['bundle_hash']==bundle['bundle_hash']
        assert result['cloud_writes']==0 and result['model_requests']==0
    assert browser['offline_api_responses'] and not browser['cloud_end_to_end_verified']
    assert browser['production_mutations']==0 and not browser['errors'] and not browser['blocked_requests']
    tests=ET.parse(folder/'captured-at-compatibility-tests.xml').getroot().find('testsuite')
    assert int(tests.attrib['tests'])==20 and int(tests.attrib['errors'])==int(tests.attrib['failures'])==0
    local=read(folder/'local-production-evidence.json')
    controls=local['health']['recovery_controls']
    sources=local['health']['collection_health']['sources']
    assert controls['hold_cloud_uploads'] and controls['pause_paid_ai']
    assert len(sources)==5 and all(s['collection_state'] in ('healthy','healthy_idle') and s['consecutive_failures']==0 for s in sources)
    assert local['formal_domains_unchanged'] and local['llm_attempts_unchanged'] and local['foreign_key_errors']==0
    purposes={'raw_item':'原Raw索引、来源与快照关联','inbox_item':'Inbox全部来源列表卡片',
        'inbox':'Inbox详情、脱敏正文及来源区块','raw_review':'原快照的Review读取模型，不提交确认'}
    summaries=[]
    forbidden={'database_identity','sender','chat_name','raw_sender_id','raw_output','local_path','protected_api_key','local_evidence_sha256','local_excerpt_spans'}
    for index,record in enumerate(bundle['records'],1):
        all_paths=list(paths(record['payload']))
        assert not any(p.split('.')[-1] in forbidden for p in all_paths)
        assert record['kind'] in purposes
        summaries.append({'number':index,'kind':record['kind'],'id':record['id'],
            'target_table':'public.eric_app_records','purpose':purposes[record['kind']],
            'creates_matter':False,'creates_task':False,'top_level_fields':list(record['payload']),
            'all_field_paths':all_paths,'source_hash':record['source_hash'],
            'utf8_payload_bytes':len(json.dumps(record['payload'],ensure_ascii=False,separators=(',',':')).encode())})
    (evidence/'projection-field-summaries.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2),encoding='utf-8')
    raws=[s['payload'] for s in samples['samples']]
    (evidence/'complete-minimized-raws.json').write_text(json.dumps(raws,ensure_ascii=False,indent=2),encoding='utf-8')
    stamp=civil(datetime.now(timezone.utc).isoformat())
    lines=['# 微信两条Raw与八条投影：最终只读审查',
        '',f'审查时间：{stamp}（北京时间）。本次只准备申请，不执行上传。',
        '',f'固定范围：2条既有Raw、8条现有记录投影、0附件。bundle_hash：`{bundle["bundle_hash"]}`。',
        '', '## 两条完整脱敏Raw',
        '', '以下为完整拟同步业务Raw的脱敏副本。原Raw索引投影只含元信息，正文存入同一条Raw关联的Inbox/Review快照；原消息、原快照ID不变。本机未脱敏原文不进入本批载荷。']
    lines += ['', '本轮冻结前发现晚自习Raw的Review.version在15:04服务再次运行后由1变2、updated_at同步变化；旧预览哈希92642d99…因此被校验拦截。正文、来源、课程和权限未变。本报告与申请草案使用重新核对后的当前8条载荷；旧版预览保留在projection-before-final-review.json作对照，不再用于申请。']
    lines += ['', '随后在现有Inbox页面预演时发现脱敏消息缺少captured_at，页面格式化日期时报Invalid time value。已仅在脱敏消息中补回原库采集时间，未修改UI、正文或读取权限。当前哈希因此从084bae10…更新为本报告顶部的908e4724…；旧载荷保留在projection-before-captured-at-compatibility.json，不再用于申请。']
    for index,raw in enumerate(raws,1):
        lines += ['',f'### Raw {index}：{raw["title"]}','','```json',json.dumps(raw,ensure_ascii=False,indent=2),'```']
    lines += ['', '## 八条投影字段摘要', '',
        '| 序号 | kind / 记录ID | 用途 | 目标表 | 新建Matter / Task |',
        '| --- | --- | --- | --- | --- |']
    for s in summaries:
        lines.append(f'| {s["number"]} | {s["kind"]} / {s["id"]} | {s["purpose"]} | {s["target_table"]} | 否 / 否 |')
    for s in summaries:
        lines += ['',f'### 投影{s["number"]}：{s["kind"]}',
            '', '**顶层字段**：'+', '.join('`'+key+'`' for key in s['top_level_fields'])+'。',
            '',f'载荷大小：{s["utf8_payload_bytes"]}字节；source_hash：`{s["source_hash"]}`。']
        if s['kind'] in ('inbox','raw_review'):
            lines.append('嵌套快照仅含选定文字、来源消息ID与时间、脱敏来源块；attachments/attachment_jobs/analysis为空，database_identity/sender/chat_name不导出。Review的draft/extraction为空，confirmed_event_ids/wechat_candidates/source_update.events/source_update.proposals为空。全部字段路径见projection-field-summaries.json，完整值见八条载荷JSON。')
    lines += ['', '## 写入与Matter / Task副作用', '',
        '正式数据库的eric_app_publish_records、begin_publish及两个记录触发器已只读核对；实际API19的worker/hello入口亦已核对。未来若获授权，本批业务载荷写eric_app_records，eric_app_changes记变更，eric_app_workers记发布epoch/generation/时间，并使用原revision序列。worker/hello还会更新既有eric_app_processors行的last_seen_at；这是此前三表说明中遗漏的元信息写入，现已补全。不会新增处理器、修改其凭据或撤销状态，也不会写登录设备/会话/RLS。没有event/task投影，没有Matter/Task表写入，没有确认动作或分析作业派发。',
        '', '本结论以未来仅发布这份固定批次为前提：不调用worker/claim、commands、capture-results、receipts、resources/files等路径，不启动默认全量publish。现有CloudSync默认publish会先处理所有资源，不能用于本批。本轮未调用任何写入路径。',
        '', '核查时这8个kind/id在远端均不存在。原发布函数会保留已有Inbox视觉字段，因此正式执行前必须重新检查目标ID；若出现意外已有内容、哈希变化或并发发布，则停止并报告，不合并未知原文。',
        '', '## 登录身份与读取范围', '',
        f'工作区owner：`{access["owner"]["email_masked"]}`；用户ID：`{access["owner"]["owner_id"]}`。已验证邮箱、authenticated角色、未删除、未封禁。',
        '', '**此前“5台设备”应更正为5个未撤销登录会话。** 它们不一定代表5台不同物理设备。五个session均存在且user_id与同一owner一致，无跨账号记录；用户本轮已确认全部由本人发起并可保留。没有新增/撤销会话或修改权限。',
        '', '| 会话设备ID前缀 | 标签 / 浏览器 | 创建时间 | 最近登记活跃 | 身份及会话核对 |',
        '| --- | --- | --- | --- | --- |']
    for d in access['devices']:
        ua=d['user_agent'];browser='Safari / iPhone' if 'iPhone' in ua else 'Edge / Windows' if 'Edg/' in ua else 'Chrome / Windows'
        lines.append(f'| {d["id"][:8]} | {d["label"]} / {browser} | {civil(d["created_at"])} | {civil(d["last_seen_at"])} | 同一owner，会话存在，未撤销；本人已确认 |')
    lines += ['', '“未撤销”不是“当前在线”：这些会话的not_after均未设置，访问仍需有效的Supabase JWT及Eric OS设备登记。未读取、导出或重放真实owner JWT，未替每台设备重新登录；当前归属由数据库/已部署访问控制代码、离线合同测试和用户识别共同核实。',
        '', '## RLS、RPC与实际访问检查', '',
        '- 已部署API确认为19；从云端重新读取源代码。API校验Supabase签名/用户、邮箱确认、工作区owner及未撤销设备，每次查询限定workspace。',
        f'- {len(acl["rls"])}张eric_app表RLS均开启，无客户端放行策略；anon/authenticated不能直接读写关键记录、资源、设备、工作区表，也不能执行{len(acl["rpcs"])}个eric_app RPC。Auth用户/会话表也未对这两种客户端角色开放。',
        '- 现网GET核查：匿名Inbox/Raw、伪造owner JWT、处理器密钥冒充owner会话、伪造文件票据及错误处理器密钥均401；现有处理器只读目录200，返回kind/id/source_hash而非正文。',
        '- 当前实际下载的API19代码合同测试33项通过，涵盖身份、独立处理器、工作区读取边界与设备撤销等路径；离线8条脱敏读模型、此前383项Python回归通过。',
        '- 本次采集时间兼容修复后，针对性20项Python测试及当前8条API19离线读模型检查通过。现有本机Inbox的“全部来源→详情→原始通知/来源块”两条样本均能展示，脱敏标识和正文匹配，页面异常0；这是本地离线响应预演，不替代正式云端端到端验收。',
        '- 当前固定8条载荷另经下载的API19发布入口离线预检：原发布RPC收到相同的kind/id/payload/source_hash；无效或撤销处理器、无效epoch/generation被拒绝，陈旧发布及结果未知返回错误且无自动重试。核对了worker/hello的处理器活跃时间副作用。数据库RPC响应为隔离夹具，未执行真实SQL，不据此声称正式去重/提交成功。',
        '- 这五个本人会话如持有效JWT，可经现有API读取该owner工作区已发布内容；目前没有群级或单条Raw读取白名单。服务端service_role、数据库管理权限及原处理器保留其已有权限，本次不扩大权限。',
        '- 设备revoked_at生效后API拒绝访问，同一已撤销session不能通过重新登记恢复。仅Supabase登出可能让已签发JWT继续有效至到期；现有API不额外以auth.sessions行存在性执行即时登出检查。这是需要保留的现有会话边界，不能宣称所有登出方式均即时失效。[Supabase说明](https://supabase.com/docs/guides/auth/signout)',
        '- 过去24小时已查到的owner认证审计事件为1次login、4次token_refreshed和4次token_revoked，浏览器类型与登记会话一致；token_revoked可来自刷新轮换，不能直接当作设备撤销或入侵。日志时间窗2026-11-03 15:30至2026-11-04 15:30，不作为全历史无异常的绝对证明。',
        '', '**结论：本次审查范围内未发现匿名、跨账号或跨工作区读取本批内容的通道，五个读取会话亦已由本人确认。** 这是当前配置和本批载荷的范围结论，不等于对所有历史访问、所有终端或服务端管理人员作绝对安全保证。',
        '', '## 本地页面预演证据', '',
        '环境为http://127.0.0.1:8765、1440×1000、Playwright Chromium。Browser plugin not available，因此使用现有Playwright；不安装依赖、不改UI、不重新构建Preview。列表与详情响应来自当前固定8条投影经实际API19 Views生成的离线结果，其他网络仅允许本机GET/HEAD，所有写请求和外网请求均被拦截。',
        '', '| 检查 | 结果 |', '| --- | --- |',
        '| 页面身份与非空内容 | Eric Inbox · Attention Desk，两条详情均正常显示 |',
        '| 框架错误层与控制台 | 无错误层，相关错误0 |',
        '| 操作路径 | 提交来源切换到全部来源，分别打开两条详情，查看原始通知与来源区块 |',
        '| 正文与脱敏标签 | 两条逐字匹配，仅脱敏节选，附件0 |',
        '| 本次写入或收费 | 生产写入0、云写入0、模型请求0 |',
        '', '证据在工作区p0-notification-evidence-20261104/inbox-minimized-preview.json与inbox-minimized-0.png、inbox-minimized-1.png。执行脚本为同目录inbox-minimized-preview.cjs。仅覆盖当前桌面Chromium两条详情，未替代手机、五个真实终端或正式云端验证。',
        '', '## 既有项目级提示', '',
        '安全检查的RLS Enabled No Policy在本链路中是客户端关闭直读、经服务端API访问的现有设计，不能为了消除提示而增加客户端读取策略。[说明](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy)',
        '', '项目还报告public中的pg_net、四个其他业务SECURITY DEFINER RPC及泄露密码保护未开启等既有提示。已核对这四个RPC无eric_app直接引用或动态EXECUTE，且含auth.uid检查；本次未调用或改动它们，不把其他业务的项目级告警声称已修复。[扩展提示](https://supabase.com/docs/guides/database/database-linter?lint=0014_extension_in_public) / [函数提示](https://supabase.com/docs/guides/database/database-linter?lint=0029_authenticated_security_definer_function_executable) / [密码保护](https://supabase.com/docs/guides/auth/password-security#password-strength-and-leaked-password-protection)',
        '', '## 当前状态', '',
        f'本机只读复核时间：{civil(local["at"])}。五个来源均健康、连续失败0，最新一轮原15分钟周期扫描完成。正式event/task/course/review_artifact完整内容哈希未变，既有微信LLM调用账本仍{local["counts_after"]["wechat_llm_attempt"]}条，外键错误0。',
        '', '本地AttentionDesk-Local采集与健康监控继续运行，云写入与DeepSeek付费分析独立暂停。本轮没有上传Raw、附件或投影，没有创建Matter/Task，也没有改变账号、会话、RLS、设备或其他业务配置。独立上传授权申请已另存草案，upload_authorized=false，未执行。']
    report=ROOT/'../../docs/diagnostics/wechat-final-raw-and-access-review-20261104.md'
    report.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    draft=['# 独立云端上传授权申请（草案，未批准、未执行）','',
        '本次只准备申请；需要后续明确授权才能执行。样本审阅及本人会话确认不视为上传批准。','',
        '拟申请范围：一次性发布两条已审阅脱敏Raw的8条现有读模型投影，附件、图片、OCR、文件元信息均为0。',
        '',f'固定载荷SHA256（bundle_hash）：`{bundle["bundle_hash"]}`。',
        '', '目标：既有Supabase项目mantuehuzptactymjpqg，工作区50e5a3ac-dd15-4f0a-a93c-ace4b735d16d。API：https://mantuehuzptactymjpqg.supabase.co/functions/v1/eric-app-api。',
        '', '主表：public.eric_app_records，kind限定raw_item/inbox_item/inbox/raw_review；原eric_app_changes及eric_app_workers记录对应变更和发布epoch/generation/时间。连接登记还会更新现有public.eric_app_processors行的last_seen_at，仅为处理器活跃时间，不改凭据或撤销状态，不新增处理器。原ID和去重机制保留，不新建表或服务。',
        '', '正文仅为“校纪校规考试安排（计划）”“晚自习学习要求”的脱敏节选；course为空并保持原状，来源/原消息/快照ID和发布时间保留。具体8条记录如下：','']
    for s in summaries:draft.append(f'- {s["kind"]} / {s["id"]}；source_hash={s["source_hash"]}')
    draft += ['', '读取边界：现有owner已验证账号及本人确认保留的5个未撤销登录会话，无新增读者或分享链接；RLS不变，无按群隔离，服务端已有管理权限不变。会话撤销/登出边界见最终审查报告。',
        '', '明确不申请：全量积压或后来新采集Raw/附件同步、任何附件或资源登记、其他来源数据发布、DeepSeek或其他付费模型请求、领取/执行云端命令、创建或确认Matter/Task、修改课程关联、变更RLS/设备/账号配置。',
        '', '执行约束：后台全量同步继续暂停；复用原客户端/接口，仅受控发送必要publisher元信息和固定8条载荷，不能调用默认全量publish。发布前再次核对原消息证据、全部字段/哈希和远端目标ID；任何内容变化、意外旧记录或并发冲突均停止，不擅自扩大范围或覆盖。',
        '', '原发布协议使用worker/hello登记发布epoch，再以generation=1一次提交8条固定记录；会接续该工作区原发布epoch。执行前须确认没有其他发布正在进行，避免干扰现有发布者。该协议已在无网络、无真实凭据的实际API19入口离线预检；现有生产暂停开关仍阻止所有写请求，离线预检不是执行入口或授权。',
        '', '验收：按原ID核对实际回执及远端去重，再通过已授权owner会话核对Eric OS API与Inbox“全部来源”的脱敏节选。失败先查已有回执，不盲目重复请求。',
        '', '风险与恢复：正式上传后，符合上述读取边界的会话及服务端管理权限可以访问文字副本；代码回滚不会撤回已上传内容。当前尚无上传，8个目标记录亦不存在。本地原文、来源ID和游标不改。',
        '', '**草案状态：未提交执行、未获得上传授权。本轮仅准备该申请，禁止根据这份文件自动上传。**']
    (report.parent/'wechat-limited-upload-authorization-draft-20261104.md').write_text('\n'.join(draft)+'\n',encoding='utf-8')
    result={'at':datetime.now(timezone.utc).isoformat(),'bundle_unchanged':True,'raw_count':2,'record_count':8,
        'attachment_count':0,'owner_sessions':len(access['devices']),'all_sessions_user_confirmed':True,
        'live_get_access_checks_passed':live['PASS'],'target_record_conflicts':0,
        'creates_matter':False,'creates_task':False,'upload_authorized':False,'draft_prepared':True,
        'local_browser_preview_passed':True,'current_targeted_tests_passed':20,
        'offline_publish_contract_passed':True,
        'cloud_end_to_end_verified':False,
        'local_service_checked_at':local['at'],'local_healthy_sources':len(sources),
        'hold_cloud_uploads':True,'pause_paid_ai':True,
        'bundle_hash':bundle['bundle_hash']}
    (evidence/'final-review-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
