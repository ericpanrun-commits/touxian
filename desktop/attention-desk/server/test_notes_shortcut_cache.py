"""Execute the native cache branch with a small deterministic file/HTTP harness.

This proves queue safety against failures, duplicates and altered files. It does
not stand in for iOS permission, installation or real network acceptance.
"""
from dataclasses import dataclass,replace
from datetime import datetime
import hashlib,plistlib,re
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parent.parent/'supabase/functions/eric-notes-ingest'
RECEIPT='11111111-1111-4111-8111-111111111111'

@dataclass
class File:
    name:str
    data:bytes
    path:str|None=None

class Native:
    def __init__(self,*,saved=True,receipt=RECEIPT,offline=False):
        self.files={};self.folders=set();self.vars={};self.outputs={};self.calls=[]
        self.deleted=[];self.notices=[];self.saved=saved;self.receipt=receipt;self.offline=offline
    def value(self,value):
        if not isinstance(value,dict):return value
        if value.get('WFSerializationType')=='WFTextTokenString':
            v=value['Value'];raw=v['string'].encode('utf-16-le')
            for key,token in sorted(v.get('attachmentsByRange',{}).items(),key=lambda row:int(re.findall(r'\d+',row[0])[0]),reverse=True):
                start,length=map(int,re.findall(r'\d+',key))
                raw=raw[:start*2]+str(self.value(token) or '').encode('utf-16-le')+raw[(start+length)*2:]
            return raw.decode('utf-16-le')
        if 'WFSerializationType' in value:return self.value(value['Value'])
        if value.get('Type')=='Variable':return self.value(value['Variable']) if 'Variable' in value else self.vars.get(value['VariableName'])
        if value.get('Type')=='ActionOutput':return self.outputs.get(value['OutputUUID'])
        if value.get('Type')=='ExtensionInput':return self.vars.get('ShortcutInput')
        if value.get('fileLocation'):return '/device'
        return value
    def run(self,name,input=None,*,cancel_cleanup=False):
        w=plistlib.loads((ROOT/name).read_bytes());self.vars={'ShortcutInput':input};self.outputs={}
        actions=w['WFWorkflowActions']
        def execute(start,end):
            i=start
            while i<end:
                a=actions[i];p=a.get('WFWorkflowActionParameters',{});kind=a['WFWorkflowActionIdentifier'].split('is.workflow.actions.')[-1]
                get=lambda key,default=None:self.value(p.get(key,default))
                out=None
                if kind in {'conditional','repeat.each'} and p['WFControlFlowMode']==0:
                    group=p['GroupingIdentifier'];finish=next(j for j in range(i+1,end) if actions[j].get('WFWorkflowActionParameters',{}).get('GroupingIdentifier')==group and actions[j]['WFWorkflowActionParameters']['WFControlFlowMode']==2)
                    otherwise=next((j for j in range(i+1,finish) if actions[j].get('WFWorkflowActionParameters',{}).get('GroupingIdentifier')==group and actions[j]['WFWorkflowActionParameters']['WFControlFlowMode']==1),finish)
                    if kind=='repeat.each':
                        for item in get('WFInput') or []:self.vars['Repeat Item']=item;execute(i+1,finish)
                    else:
                        v=get('WFInput');code=p['WFCondition']
                        yes=bool(v) if code==100 else (float(v or 0)>p['WFNumberValue'] if code==2 else (v!=get('WFConditionalActionString') if code==5 else float(v or 0)==p['WFNumberValue']))
                        execute(i+1,otherwise) if yes else execute(otherwise+1,finish)
                    i=finish+1;continue
                if kind=='gettext':out=get('WFTextActionText')
                elif kind=='setvariable':self.vars[p['WFVariableName']]=get('WFInput')
                elif kind=='appendvariable':self.vars[p['WFVariableName']]=(self.vars.get(p['WFVariableName']) or [])+[get('WFInput')]
                elif kind=='nothing':out=None
                elif kind=='number':out=float(get('WFNumberActionNumber') or 0)
                elif kind=='file.createfolder':out=get('WFFolder')+'/'+get('WFFilePath');self.folders.add(out)
                elif kind=='file.getfoldercontents':
                    folder=get('WFFolder');out=[replace(f,path=path) for path,f in self.files.items() if path.rsplit('/',1)[0]==folder]
                    out += [f for f in self.folders if f.rsplit('/',1)[0]==folder]
                elif kind=='getitemname':out=get('WFInput').name
                elif kind=='setitemname':
                    v=get('WFInput');out=replace(v,name=get('WFName')) if isinstance(v,File) else File(get('WFName'),str(v).encode())
                elif kind=='documentpicker.save':
                    v=get('WFInput');path=get('WFFolder')+'/'+v.name
                    self.files[path]=replace(v,path=path);out=self.files[path]
                elif kind=='documentpicker.open':
                    path=get('WFFile')+'/'+get('WFGetFilePath');out=self.files.get(path)
                    if out is None:assert p['WFFileErrorIfNotFound'] is False
                elif kind=='properties.files':
                    v=get('WFInput');prop=p['WFContentItemPropertyName']
                    out=(v.name.rsplit('.',1)[-1] if isinstance(v,File) else '') if prop=='File Extension' else '2026-10-29T08:30:00+08:00'
                elif kind=='hash':
                    v=get('WFInput');out=hashlib.sha256(v.data if isinstance(v,File) else str(v).encode()).hexdigest()
                elif kind=='detect.text':out=get('WFInput').data.decode()
                elif kind=='text.match':out=re.findall(get('WFMatchTextPattern'),str(get('text')))
                elif kind=='count':out=len(get('Input') or [])
                elif kind=='urlencode':out=get('WFInput')
                elif kind=='format.date':out=get('WFDate')
                elif kind=='getvalueforkey':out=(get('WFInput') or {}).get(p['WFDictionaryKey'])
                elif kind=='downloadurl':
                    v=get('WFRequestVariable');self.calls.append(v.data)
                    if self.offline:raise ConnectionError('Native iOS action stops; cache must remain durable.')
                    out={'saved':self.saved,'receipt_id':self.receipt}
                elif kind=='notification':self.notices.append(get('WFNotificationActionBody'))
                elif kind=='file.delete':
                    self.deleted.append([v.path for v in get('WFInput')])
                    if not cancel_cleanup:
                        for path in self.deleted[-1]:self.files.pop(path)
                else:raise AssertionError('Unhandled native action: '+kind)
                if 'UUID' in p:self.outputs[p['UUID']]=out
                i+=1
        execute(0,len(actions))

def note():return File('无穷小 📝.pdf',b'%PDF-1.7\nClassroom source, cache safety only.\n%%EOF')
def pdfs(h):return [path for path in h.files if path.lower().endswith('.pdf')]

def test_saved_ack_keeps_cache_and_next_run_skips_network():
    h=Native();original=note();h.run('shortcut-source.plist',original)
    assert len(pdfs(h))==1 and not h.deleted and len(h.calls)==1
    assert original.path is None and original.data==h.calls[0]
    assert any('接收回执/' in path for path in h.files)
    h.run('shortcut-source.plist')
    assert len(h.calls)==1 and h.notices[-1]=='Eric OS 已接收'

@pytest.mark.parametrize('case',['saved_false','bad_receipt','offline'])
def test_unconfirmed_uploads_keep_pending_and_retry(case):
    h=Native(saved=case!='saved_false',receipt='invalid' if case=='bad_receipt' else RECEIPT,offline=case=='offline')
    try:h.run('shortcut-source.plist',note())
    except ConnectionError:assert case=='offline'
    assert len(pdfs(h))==1 and not h.deleted
    assert not any('接收回执/' in path for path in h.files)
    h.saved=True;h.receipt=RECEIPT;h.offline=False
    h.run('shortcut-source.plist');assert len(h.calls)==2
    assert any('接收回执/' in path for path in h.files)

def test_cleanup_requires_own_cache_ack_and_unchanged_content_and_one_batch():
    h=Native();h.run('shortcut-source.plist',note())
    eligible=pdfs(h)[0]
    h.files['/outside/original.pdf']=File('original.pdf',note().data,'/outside/original.pdf')
    # A legacy/manual queue file may upload but lacks proof it was our generated cache.
    legacy='/device/Eric OS 待发送/legacy.pdf';h.files[legacy]=File('legacy.pdf',note().data,legacy)
    h.run('shortcut-source.plist')
    assert len(h.calls)==2
    h.run('cleanup-source.plist',cancel_cleanup=True)
    assert h.deleted==[[eligible]] and eligible in h.files
    h.run('cleanup-source.plist')
    assert eligible not in h.files and legacy in h.files and '/outside/original.pdf' in h.files
    assert len(h.calls)==2

def test_altered_cache_does_not_reuse_prior_receipt_or_cleanup_permission():
    h=Native();h.run('shortcut-source.plist',note());path=pdfs(h)[0]
    h.files[path]=replace(h.files[path],data=b'%PDF-1.7\nChanged bytes\n%%EOF')
    h.run('cleanup-source.plist');assert path in h.files and not h.deleted
    h.run('shortcut-source.plist');assert len(h.calls)==2
    h.run('cleanup-source.plist');assert path in h.files and not h.deleted
