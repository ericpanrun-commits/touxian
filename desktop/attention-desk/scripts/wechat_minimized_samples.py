"""Prepare explicit, source-backed local review samples; never upload or spend."""
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from wechat_backlog_plan import ReadOnlyStore
from server.personal_store import default_database_path
from server.cloud_sync.minimization import selected_text_preview
from server.cloud_sync.minimized_projection import minimized_projection_preview

SELECTIONS=[
    {'raw_item_id':'d5fc2590-6e46-4a2e-8489-26094fd8e6c0','snapshot_id':'5670fdb2-c494-401d-8108-85d99c46aa5c',
     'title':'校纪校规考试安排（计划）','reason':'本班学校事务，含未来考试日期；不包含同批次中的可选活动及联系方式。',
     'schedule_quotes':['11月18日周三晚修时间'],
     'messages':[{'id':'d80a33aa-d113-4079-9503-c501ac9ab535','excerpts':[
         '学院计划在11月18日周三晚修时间，在晚修课室进行校纪校规考试，请大家合理安排时间，准时参加。',
         '另外请大家及时查阅学生手册，熟悉相关校纪校规。']}]},
    {'raw_item_id':'1b26a8b3-cd55-4284-be7f-36cd20c26da2','snapshot_id':'91a3c166-c39f-4831-8930-06a683fcd662',
     'title':'晚自习学习要求','reason':'本人为2026级学生，保留适用的学习要求；去除班干部签到职责、宿舍检查及评奖内容。',
     'messages':[{'id':'521fd588-e436-49f4-a2e7-5ddf0ac0b2f9','excerpts':[
         '学生在晚自习教室自主完成作业，预复习课程知识。',
         '学生自愿存放手机。不强制统一上交',
         '严禁迟到、早退、玩手机等违禁行为。']}]}]


def main():
    personal=ReadOnlyStore(default_database_path())
    result=selected_text_preview(personal,SELECTIONS)
    bundle=minimized_projection_preview(personal,result)
    result['generated_at']=datetime.now(timezone.utc).isoformat()
    result['plan']='First review batch only; excludes attachments, OCR, unrelated chat, sensitive affairs and unverified course mappings.'
    output=ROOT/'artifacts/p0-20261104/wechat-minimized-text-samples.json'
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    output.with_name('wechat-minimized-projection-preview.json').write_text(json.dumps(bundle,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:result[key] for key in ('preview_only','upload_authorized','model_requests','attachment_count','selected_raw_count','proposed_raw_count','planned_record_count')}))


if __name__=='__main__':main()
