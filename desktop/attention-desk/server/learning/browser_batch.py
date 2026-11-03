"""One bounded batch using the existing browser, file store and learning jobs."""
import asyncio,json,os,subprocess,sys
from datetime import datetime,date
from pathlib import Path
from zoneinfo import ZoneInfo
from ..personal_store import PersonalError,encode,now
from .store import unpack
from .browser_ingest import BrowserIntake


class BrowserBatch:
    def __init__(self,service):
        self.service,self.store,self.personal=service,service.store,service.personal
        self.documents=getattr(service.school,'documents',None)

    def folder(self,artifact_id):
        root=(self.documents.files.work/'learning-browser').resolve()
        target=(root/artifact_id).resolve()
        if not target.is_relative_to(root):raise PersonalError('读取目录无效。')
        return target

    def status(self):
        with self.personal.connection() as c:
            row=c.execute("SELECT * FROM learning_artifact WHERE kind='browser_batch' ORDER BY rowid DESC LIMIT 1").fetchone()
        if not row:return {'status':'idle','message':'登录学习通后，一次读取已提交的预习与课堂题。'}
        item=unpack(row,'payload_json','error_json');payload=item['payload'] or {}
        status=item['status'];message=payload.get('message','上次读取已停止，原件保留。')
        if status=='running':
            try:
                progress=json.loads((self.folder(item['id'])/'progress.json').read_text(encoding='utf-8'))
                if progress.get('status') in {'running','waiting_foreground','captured','needs_attention'}:
                    message=progress.get('message') or message
            except (OSError,ValueError):pass
        return {'status':status,'message':message,'started_at':item['created_at'],'finished_at':payload.get('finished_at'),
            'counts':payload.get('counts'),'partial':payload.get('partial',False),'busy':status=='running'}

    def pending_days(self,day):
        with self.personal.connection() as c:
            values=[r[0] for r in c.execute("""SELECT DISTINCT COALESCE(json_extract(cap.context_json,'$.platform_activity_day'),substr(json_extract(cap.context_json,'$.activity_published_at'),1,10))
                FROM learning_attempt a JOIN learning_capture cap ON cap.id=a.capture_id
                WHERE a.result='unknown' AND json_extract(cap.context_json,'$.activity_type')='classroom'
                AND json_extract(cap.context_json,'$.native_course_name')='高等数学Ⅰ1'
                AND NOT EXISTS(SELECT 1 FROM learning_attempt newer WHERE newer.supersedes_id=a.id)""") if r[0]]
        recent=[]
        for value in values:
            try:
                if 0<=(date.fromisoformat(day)-date.fromisoformat(value)).days<=14:recent.append(value)
            except ValueError:continue
        return sorted(set(recent),reverse=True)[:2]

    def start(self,request_id):
        current=self.status()
        if current.get('busy'):return current
        artifact=self.store.reserve_artifact('browser_batch','readonly-browser:'+str(request_id))
        if artifact['status']!='running':return self.status()
        value={'message':'正在寻找已登录的学习通课程窗口。','requested_cycle_date':datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()}
        with self.personal.connection(write=True) as c:c.execute('UPDATE learning_artifact SET payload_json=? WHERE id=?',(encode(value),artifact['id']))
        self.service.schedule('browser:'+artifact['id'],lambda:self.run(artifact['id'],value['requested_cycle_date']))
        return self.status()

    async def run(self,artifact_id,day):
        directory=self.folder(artifact_id);directory.mkdir(parents=True,exist_ok=True)
        output=directory/'result.json';helper=None
        try:
            env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'}
            command=[sys.executable,'-m','server.learning.browser_capture','--batch','--day',day,'--output',str(output)]
            for pending_day in self.pending_days(day):
                if pending_day!=day:command.extend(['--pending-day',pending_day])
            helper=subprocess.Popen(command,
                cwd=str(Path(__file__).resolve().parents[2]),env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:await asyncio.to_thread(helper.communicate,timeout=325)
            except subprocess.TimeoutExpired:
                helper.kill();await asyncio.to_thread(helper.communicate)
            progress={}
            try:progress=json.loads((directory/'progress.json').read_text(encoding='utf-8'))
            except (OSError,ValueError):pass
            path=output if output.is_file() else directory/'partial.json'
            if not path.is_file():
                raise PersonalError(progress.get('message') or '本次没有读到结果，请检查现有学习通窗口仍已登录，再重试。','browser_not_captured',409)
            packet=json.loads(path.read_text(encoding='utf-8'))
            result=await BrowserIntake(self.service,self.documents).ingest(packet,directory)
            if packet.get('partial'):
                result['message']='已保留读到的部分结果。'+(progress.get('message') or '有页面尚未读完，可稍后重试。')
            result['finished_at']=now()
            self.store.finish_artifact(artifact_id,result,status='needs_review' if packet.get('partial') else 'completed')
        except asyncio.CancelledError:
            if helper and helper.poll() is None:
                helper.terminate();await asyncio.to_thread(helper.communicate)
            self.store.finish_artifact(artifact_id,{'message':'本次读取已停止；已有原件保留，可重新启动。','finished_at':now()},status='interrupted')
            raise
        except Exception as error:
            message=str(error) if isinstance(error,PersonalError) else '读取没有完成，已有原件保留；请检查学习通窗口后重试。'
            self.store.finish_artifact(artifact_id,{'message':message,'finished_at':now()},status='needs_review',error={'message':message})
