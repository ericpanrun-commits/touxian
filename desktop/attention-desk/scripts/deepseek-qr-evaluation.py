"""Three fictional QR-text cases; never read chats or send attachments.

Reuses the paid evaluation's exclusive request receipts: uncertain results are
not resent. Only --execute permits these three text requests to DeepSeek.
"""
import argparse
import asyncio
import json
from pathlib import Path
import runpy

ROOT=Path(__file__).resolve().parents[1]
CASES=[
    ('qr-required','请全体同学于2026年11月10日12:00前扫描二维码完成活动登记。集合时间：2026年11月11日09:00。地点：教学楼一楼。二维码见群内图片。'),
    ('qr-optional','学校图书馆自愿讲座开放报名。有意参加的同学请于2026年11月12日18:00前扫码填写报名表，讲座地点为图书馆报告厅。二维码见群内图片。'),
    ('qr-incomplete','请扫码，具体事项见图片。'),
]


def evaluator():
    module=runpy.run_path(str(ROOT/'scripts/deepseek-reviewed-evaluation.py'))
    ns=module['main'].__globals__
    from server.wechat_collect.qr_text_policy import scan_notice,INSTRUCTION
    ns['OUT']=ROOT/'artifacts/deepseek-qr-20261108/live-fictional'
    ns['prepare']=lambda:[{'id':key,'text':text,'source_time':'2026-11-08T22:00:00+08:00',
        'time_reliability':'reliable'} for key,text in CASES]
    original_inputs=ns['inputs'];original_body=ns['build_text_request']
    def inputs(sample):
        batch,snapshot,context=original_inputs(sample)
        for message in snapshot['wechat']['messages']:message['captured_at']=sample['source_time']
        return batch,snapshot,context
    def body(config,batch,snapshot,context):
        value=original_body(config,batch,snapshot,context)
        if scan_notice(snapshot['wechat']['messages']):value['messages'][0]['content']+=INSTRUCTION
        return value
    ns['inputs']=inputs;ns['build_text_request']=body
    return ns


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--replay-only',action='store_true')
    args=parser.parse_args();ns=evaluator()
    asyncio.run(ns['main'](args.execute or args.replay_only,replay_only=args.replay_only))
    print(json.dumps({'synthetic_only':True,'real_messages_read':0,'images_sent':0,'maximum_requests':3}))
