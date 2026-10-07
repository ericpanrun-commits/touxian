"""Two bounded local parser workers. Persisted jobs never create formal items."""
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from ..personal_store import identifier, now, encode, PersonalError
from .storage import AttachmentStore
from .policy import validate_file, SOFT_BYTES, DocumentError, MIMES
from .downloader import download


class DocumentService:
    def __init__(self, personal):
        self.personal = personal
        self.files = AttachmentStore(personal)
        self.executor = ThreadPoolExecutor(max_workers=2,thread_name_prefix='local-documents')
        self.lock = threading.Lock()
        self.children = set()
        self.stopping = threading.Event()
        self.files.seed_existing()
        with personal.connection(write=True) as c:
            c.execute("UPDATE attachment_job SET status='failed',phase='interrupted',error_json=?,completed_at=? WHERE status IN ('waiting','running')",(encode([{'code':'interrupted','message':'解析因应用退出中断，原文件保留，请主动重试。'}]),now()))
            c.execute("UPDATE attachment SET parse_status='failed',parse_error='解析因应用退出中断，原文件保留，请主动重试。' WHERE parse_status='parsing'")

    def start(self, snapshot_id, attachment_ids, confirm_large=False, redownload=False):
        with self.personal.connection(write=True) as c:
            source = c.execute('SELECT * FROM raw_snapshot WHERE id=?',(snapshot_id,)).fetchone()
            if not source: raise PersonalError('原文不存在。','not_found',404)
            if not attachment_ids or len(set(attachment_ids))>10: raise PersonalError('请每次选择 1 至 10 个文件。')
            active = c.execute("SELECT * FROM attachment_job WHERE raw_item_id=? AND status IN ('waiting','running')",(source['raw_item_id'],)).fetchone()
            if active: return dict(active)
            allowed = {r[0] for r in c.execute('SELECT attachment_id FROM snapshot_attachment WHERE raw_snapshot_id=?',(snapshot_id,))}
            if set(attachment_ids)-allowed: raise PersonalError('附件不属于这份原文。','invalid_reference',400)
            for aid in attachment_ids:
                att = dict(c.execute('SELECT * FROM attachment WHERE id=?',(aid,)).fetchone())
                if (att['size'] or 0)>SOFT_BYTES and not confirm_large: raise PersonalError('文件过大，需要确认。','large_file_confirmation',413)
            job = identifier()
            c.execute('''INSERT INTO attachment_job (id,raw_item_id,source_snapshot_id,attachment_ids_json,operation,status,phase,created_at)
                VALUES (?,?,?,?,?,'waiting','等待本地解析',?)''',(job,source['raw_item_id'],snapshot_id,encode(attachment_ids),'redownload' if redownload else 'parse',now()))
        self.executor.submit(self._run,job,snapshot_id,attachment_ids,confirm_large,redownload)
        return {'id':job,'status':'waiting'}

    def worker(self,path,ext,destination,timeout=180):
        flags = subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0
        child = subprocess.Popen([sys.executable,'-X','utf8','-m','server.documents.worker',str(path),ext,str(destination)],
                                 cwd=Path(__file__).resolve().parents[2],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
        with self.lock: self.children.add(child)
        try:
            deadline = time.monotonic()+timeout
            while child.poll() is None:
                if self.stopping.wait(.15) or time.monotonic()>deadline:
                    child.kill(); child.wait(timeout=5)
                    raise DocumentError('parse_timeout','本地解析中断或超过时间上限，原文件保留。')
            if not Path(destination).exists(): raise DocumentError('parse_failed','文件无法解析或超出本地资源上限，原文件保留。')
        finally:
            with self.lock: self.children.discard(child)

    def _phase(self, job, phase):
        with self.personal.connection(write=True) as c: c.execute("UPDATE attachment_job SET status='running',phase=? WHERE id=?",(phase,job))

    def _run(self,job,snapshot_id,attachment_ids,confirm_large,redownload):
        parsed, errors, resolved_ids = [], [], []
        work = self.files.work / job; work.mkdir(exist_ok=True)
        for aid in attachment_ids:
            att = self.files.get(aid)
            target = aid
            try:
                if self.stopping.is_set(): raise DocumentError('interrupted','解析中断，原文件保留。')
                if not att['local_path'] or (redownload and att['original_url']):
                    self._phase(job,'下载原文件')
                    with self.personal.connection() as c:
                        source = c.execute('SELECT o.config_json FROM official_source o JOIN raw_item r ON r.source_id=o.source_id WHERE r.id=?',(att['raw_item_id'],)).fetchone()
                    if not source: raise DocumentError('invalid_source','未找到附件的官网来源配置。')
                    mime = download(att['original_url'],json.loads(source[0])['allowed_hosts'],work/'download.bin',confirm_large=confirm_large)
                    inferred = next((ext for ext,type_ in MIMES.items() if type_==mime.strip()),None) if not att['extension'] else None
                    att = self.files.downloaded(aid,work/'download.bin',mime,inferred); target=att['id']
                    # A hash cache hit still has to satisfy this download's MIME policy.
                    validate_file(work/'download.bin',att['extension'],mime)
                if att['parse_status']=='parsed': parsed.append(target); continue
                validate_file(self.files.path(att),att['extension'],att['mime_type'])
                self._phase(job,'本地解析 / OCR（图片不上传云端）')
                with self.personal.connection(write=True) as c: c.execute("UPDATE attachment SET parse_status='parsing',parse_error='' WHERE id=?",(target,))
                destination = work/f'{target}.json'
                self.worker(self.files.path(att),att['extension'],destination)
                result=json.loads(destination.read_text(encoding='utf-8'))
                if result.get('error'): raise DocumentError(result['error']['code'],result['error']['message'])
                meta=result['metadata']
                with self.personal.connection(write=True) as c:
                    c.execute("UPDATE attachment SET parse_status='parsed',extracted_text=?,extractor=?,extractor_version=?,parse_manifest_json=?,needs_review=? WHERE id=?",(result['extracted_text'],meta['extractor'],meta['extractor_version'],encode(result),int(meta['needs_review']),target))
                parsed.append(target)
            except (DocumentError,PersonalError) as e:
                failed = self.files.get(target)
                errors.append({'attachment_id':target,'code':e.code,'message':str(e),'local_saved':bool(failed['local_path']),'filename':failed['filename']})
                with self.personal.connection(write=True) as c:
                    c.execute("UPDATE attachment SET parse_status=?,parse_error=? WHERE id=? AND parse_status!='parsed'",('unsupported' if e.code=='unsupported' else 'failed',str(e),target))
            except Exception:
                errors.append({'attachment_id':target,'code':'parse_failed','message':'文件解析失败，原文件保留。'})
                with self.personal.connection(write=True) as c: c.execute("UPDATE attachment SET parse_status='failed',parse_error='文件解析失败，原文件保留。' WHERE id=? AND parse_status!='parsed'",(target,))
            finally:
                resolved_ids.append(target)
        result_snapshot = None
        try: result_snapshot=self.files.compose(snapshot_id,resolved_ids)
        except PersonalError as e: errors.append({'code':e.code,'message':str(e)})
        with self.personal.connection(write=True) as c:
            c.execute('UPDATE attachment_job SET status=?,phase=?,result_snapshot_id=?,error_json=?,completed_at=? WHERE id=?',('partial' if errors and result_snapshot else 'failed' if errors else 'completed','解析完成' if result_snapshot else '需重新解析',result_snapshot,encode(errors) if errors else None,now(),job))

    def job(self, job_id):
        with self.personal.connection() as c:
            row=c.execute('SELECT * FROM attachment_job WHERE id=?',(job_id,)).fetchone()
            if not row: raise PersonalError('解析任务不存在。','not_found',404)
            result=dict(row); result['error']=json.loads(result.pop('error_json') or 'null'); result['attachment_ids']=json.loads(result.pop('attachment_ids_json'))
            if isinstance(result['error'],dict): result['error']=[result['error']]
            return result

    def close(self):
        self.stopping.set()
        with self.lock:
            for child in self.children:
                if child.poll() is None: child.kill()
        self.executor.shutdown(wait=True,cancel_futures=True)
