"""Normalize the trusted local reader's output into the existing evidence ledger."""
import asyncio,hashlib,json,re
from datetime import datetime
from pathlib import Path
from uuid import NAMESPACE_URL,uuid5
from ..personal_store import PersonalError,encode,now
from ..extraction_models import RawInput
from ..review_store import ReviewStore
from ..inbox_store import InboxStore
from .models import CapturedPage,CapturedQuestion,PreviewMaterial
from .store import unpack
from .rules import normalized
from .browser_parse import grade_page
from .browser_capture import safe_text

def digest(value):return hashlib.sha256(encode(value).encode()).hexdigest()
def request_key(value):return str(uuid5(NAMESPACE_URL,'eric-readonly-browser:'+value))
def base_title(title):return re.sub(r'(?:的?定义)?(?:测试题|测验题|练习题|测验|测试)$','',title).strip()
def canonical_options(options):return {k:re.sub(r'\s+','',normalized(v)).replace('−','-').replace('–','-') for k,v in options.items()}

class BrowserIntake:
    def __init__(self,service,documents):
        self.service,self.personal,self.documents=service,service.personal,documents
        self.store=service.store;self.review=ReviewStore(self.personal);self.inbox=InboxStore(self.personal)

    def aliases(self,course,key,published=None):
        with self.personal.connection() as c:
            explicit=c.execute("SELECT payload_json FROM learning_artifact WHERE kind='browser_activity_alias' AND json_extract(payload_json,'$.native_activity_key')=? ORDER BY rowid DESC LIMIT 1",(key,)).fetchone()
            alias=json.loads(explicit[0] or '{}') if explicit else {}
            for row in c.execute('SELECT * FROM learning_capture ORDER BY created_at DESC'):
                capture=unpack(row,'context_json','parsed_json','error_json');ctx=capture['context']
                if ctx.get('course_id')!=course:continue
                if ctx.get('native_activity_key')==key or capture['id']==alias.get('legacy_capture_id') or (published and ctx.get('activity_type')=='classroom' and ctx.get('activity_published_at')==published):return capture
        return None

    def source(self,course,record,body,root):
        files=[]
        for image in record.get('images',[])[:3]:
            path=(root/image['file']).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=image['sha256']:raise PersonalError('读取图像的来源发生变化，未保存为证据。','source_changed',409)
            files.append((path.name,'image/png',path))
        identity=request_key(digest([course,record['activity_type'],record['activity_name'],body,[(n,hashlib.sha256(p.read_bytes()).hexdigest()) for n,_,p in files]]))
        title='学习通 · '+record['activity_name']
        source={'source_id':'xuexitong_readonly','source_kind':'learning_page','source_name':'学习通 · 本人已提交记录（只读）','raw_kind':'learning_page'}
        if files:bundle=self.documents.files.collect(files,body,title,None,identity,**source)
        else:bundle=self.review.collect(RawInput(original_text=body,title_hint=title),identity,**source)
        raw,sid=bundle['snapshot']['raw_item_id'],bundle['snapshot']['id']
        with self.personal.connection(write=True) as c:
            existing=c.execute('SELECT inbox_course_id FROM raw_item WHERE id=?',(raw,)).fetchone()[0]
            if existing and existing!=course:raise PersonalError('已有原件的手动课程关联不同，请核对后继续。','course_conflict',409)
            c.execute('UPDATE raw_item SET inbox_course_id=? WHERE id=? AND inbox_course_id IS NULL',(course,raw))
        self.inbox.state(raw,'completed','learning_readonly_saved',snapshot_id=sid)
        return raw,sid

    async def option_text(self,record,root):
        values={}
        for image in record.get('option_images',[])[:32]:
            path=(root/image['file']).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=image['sha256']:continue
            destination=root/(path.stem+'-ocr.json')
            try:
                await asyncio.to_thread(self.documents.worker,path,'png',destination,45)
                result=json.loads(destination.read_text(encoding='utf-8'))
                text=' '.join(b.get('text','') for b in result.get('blocks',[])).strip()
                text=safe_text(text)
                if 0<len(text)<=120:values.setdefault(str(image['number']),{})[image['letter']]=text
            except Exception:continue
        return values

    def materials(self,record,day):
        lines=[str(s).strip() for p in record['pages'] for s in p['text'] if str(s).strip()]
        topic=normalized(base_title(record['activity_name'])).replace('的','')
        result=[]
        for i,title in enumerate(lines[:-3]):
            if not re.search(r'\.(?:mp4|pdf|pptx?|docx?)(?:\s*)$',title,re.I):continue
            name=normalized(re.sub(r'\([^)]*\)|（[^）]*）|\.[a-z0-9]+$','',title,flags=re.I)).replace('的','')
            if len(topic)<3 or topic not in name:continue
            chunk=lines[i:i+4];complete=chunk[2]=='已完成'
            if chunk[2] not in {'已完成','未完成'}:continue
            completed=None
            if complete and re.fullmatch(r'\d\d-\d\d\s+\d\d:\d\d',chunk[3]):
                try:completed=datetime.fromisoformat(day[:5]+chunk[3]).replace(tzinfo=datetime.fromisoformat(day+'T00:00:00+08:00').tzinfo).isoformat()
                except ValueError:pass
            material=PreviewMaterial(title=title,kind='video' if re.search(r'\.mp4$',title,re.I) else 'slides' if re.search(r'\.pptx?$',title,re.I) else 'reading',completed=complete,
                evidence_quote='\n'.join(chunk),completed_at=completed,source_reference=record['pages'][0]['source_url'])
            if not any(m.title==material.title and m.completed_at==material.completed_at for m in result):result.append(material)
        return result

    def material_already_saved(self,course,material):
        with self.personal.connection() as c:
            for row in c.execute('SELECT parsed_json FROM learning_capture WHERE json_extract(context_json,\'$.course_id\')=?',(course,)):
                for prior in json.loads(row[0] or '{}').get('preview_materials',[]):
                    if prior.get('title')==material.title and prior.get('completed')==material.completed and prior.get('completed_at')==material.completed_at:return True
        return False

    async def ingest(self,packet,root,course='course-math-ii'):
        if packet.get('schema')!='readonly-native-batch-v1' or packet.get('native_course_name')!='高等数学Ⅰ1' or packet.get('native_class_name')!='《高等数学Ⅰ1》4班':raise PersonalError('读取结果与已确认课程不一致，未强行保存。','course_context_mismatch',409)
        self.store.sync_topics();day=packet['requested_cycle_date'];saved=[];counts={'preview':{'total':0,'correct':0,'incorrect':0,'unknown':0},'classroom':{'total':0,'correct':0,'incorrect':0,'unknown':0}}
        new_ids=[];duplicates=0
        for record in packet['records']:
            kind=record['activity_type'];pages=record['pages']
            body='高等数学 II\n原生课程：'+packet['native_course_name']+'\n原生班级：'+packet['native_class_name']+'\n活动：'+record['activity_name']+'\n\n'+'\n\n'.join('\n'.join(p['text']) for p in pages)
            published=None
            if kind=='classroom':
                head='\n'.join(pages[0]['text'][:25]);match=re.search(r'(?m)^\s*(\d\d-\d\d\s+\d\d:\d\d)\s*$',head)
                if match:published=record.get('activity_day',day)[:5]+match[1].replace(' ','T')+':00+08:00'
            native_key=digest([course,kind,published if kind=='classroom' else record.get('chapter',''),record['activity_name']])
            prior=self.aliases(course,native_key,published)
            ctx={'course_id':course,'course_basis':'previously_confirmed_native_course','activity_type':'preview' if kind.startswith('preview') else 'classroom',
                'activity_id':prior['context'].get('activity_id') if prior else 'native-'+native_key[:32],'native_activity_key':native_key,
                'native_course_name':packet['native_course_name'],'native_class_name':packet['native_class_name'],'requested_cycle_date':day,
                'capture_method':'readonly_verified_capture','quality':'text_extracted','source_access':'existing_logged_in_browser_in_place','no_credentials_collected':True}
            if kind.startswith('preview'):
                ctx.update(preview_title=base_title(record['activity_name']),preview_session_id=prior['context'].get('preview_session_id') if prior else 'preview-native-'+digest([course,record.get('chapter',''),record['activity_name']])[:24],session_basis='native_course_chapter')
                if prior:
                    for field in ['target_lecture_date','temporal_relation','relation_source','lecture_id','lecture_basis']:
                        if prior['context'].get(field):ctx[field]=prior['context'][field]
            if published:ctx['activity_published_at']=published
            if kind=='classroom' and record.get('activity_day'):ctx['platform_activity_day']=record['activity_day']
            if kind=='preview_material':
                materials=self.materials(record,day);new_materials=[m for m in materials if not self.material_already_saved(course,m)]
                raw,sid=self.source(course,record,body,root)
                if new_materials:
                    cap=self.store.record_capture(raw,sid,body,ctx);self.store.save_page(cap,CapturedPage(preview_materials=new_materials),trusted=True)
                saved.append({'raw_item_id':raw,'snapshot_id':sid,'materials':len(materials),'new_materials':len(new_materials)});continue
            parsed=[grade_page(p['text'],kind) for p in pages];questions=[q for part in parsed for q in part['questions']]
            unique={}
            for q in questions:unique[(q['number'],q['question'])]=q
            questions=list(unique.values())
            if not questions:
                self.source(course,record,body,root)
                raise PersonalError('原页已保存，但这次页面布局未能可靠辨认已提交题目，未新增作答。','unrecognized_result_layout',409)
            for q in questions:
                if q['my_answer']:counts[kind]['total']+=1;counts[kind][q['result']]+=1
            conflict=any(p['publication_conflict'] for p in parsed)
            ctx.update(result_origin=parsed[0]['result_origin'] if parsed else 'platform_result_text',answer_key_status='publication_conflict' if conflict else 'displayed',
                result_notice='页面公布状态冲突，保留作答并等待确认；积分未作成绩。' if conflict else '结果只取页面已批阅分数、本人结果或已显示答案；活动发布时间不是本人提交时间。')
            prior_questions=(prior.get('parsed') or {}).get('questions',[]) if prior else []
            unchanged=bool(prior and len(questions)==len(prior_questions) and all(any(normalized(q['question'])==normalized(p['question']) and q['my_answer']==p.get('my_answer') and q['result']==p.get('result') for p in prior_questions) for q in questions) and (prior['context'].get('answer_key_status')=='publication_conflict')==conflict)
            ocr=await self.option_text(record,root) if not unchanged else {}
            if ocr:
                body+='\n\n本地选项识别补记（未核验，不用于判分）：\n'+json.dumps(ocr,ensure_ascii=False)
                ctx['options_quality']='ocr_unverified'
            if kind=='classroom' and prior and prior['context'].get('independent') and all(any(normalized(q['question'])==normalized(p['question']) and q['my_answer']==p.get('my_answer') for p in prior_questions) for q in questions):
                for field in ['independent','independence_source','independence_statement','lecture_id','lecture_basis']:ctx[field]=prior['context'].get(field)
            rows=[]
            for q in questions:
                q['options']=q['options'] or ocr.get(str(q['number']),{})
                rows.append(CapturedQuestion(**{k:q[k] for k in ['question','options','my_answer','correct_answer','result','result_quote','explanation','submitted_at']}))
            raw,sid=self.source(course,record,body,root)
            if unchanged:duplicates+=sum(bool(q['my_answer']) for q in questions)
            else:
                cap=self.store.record_capture(raw,sid,body,ctx)
                if cap['status']=='received':new_ids.extend(self.store.save_page(cap,CapturedPage(questions=rows),trusted=True))
            saved.append({'raw_item_id':raw,'snapshot_id':sid,'questions':len(questions),'unchanged':unchanged})
        for aid in dict.fromkeys(new_ids):self.service.schedule('mistake:'+aid,lambda aid=aid:self.service.prepare_mistake(aid))
        return {'counts':counts,'duplicate_attempts_skipped':duplicates,'sources':saved,'partial':packet.get('partial',False),'message':'本次读取已保存；未公布的结果保留待确认，没有操作学习通答题或进度。'}
