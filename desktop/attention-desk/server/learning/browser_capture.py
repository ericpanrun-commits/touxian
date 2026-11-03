"""Bounded read-only navigation in the owner's existing Edge course window.

Reuses the installed Windows UIA dependency. Never opens a browser/profile,
reads cookie storage, enters an answer, submits, or opens learning media.
"""
from __future__ import annotations
import argparse,json,re,time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from .browser_parse import question_blocks,is_ended_exercise,submitted_result

class CaptureStopped(Exception):pass

def safe_text(value):
    def link(match):
        try:
            parsed=urlsplit(match[0]);return parsed.scheme+'://'+parsed.hostname+parsed.path
        except (ValueError,TypeError):return '[URL omitted]'
    value=re.sub(r'https?://[^\s<>]+',link,str(value or ''))
    return re.sub(r'(?i)(cookie|session(?:_token)?|token|authorization|password|密码|验证码)\s*[:=]\s*\S+',r'\1: [omitted]',value)

def safe_location(value):
    try:
        parsed=urlsplit(value)
        if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in {None,443} or not (parsed.hostname=='chaoxing.com' or (parsed.hostname or '').endswith('.chaoxing.com')):raise ValueError()
        return parsed.scheme+'://'+parsed.hostname+parsed.path
    except ValueError:raise CaptureStopped('请在现有Edge窗口打开已登录的学习通课程页。')

class EdgeReader:
    def __init__(self,course='高等数学Ⅰ1',class_name='《高等数学Ⅰ1》4班'):
        import uiautomation as auto
        self.auto,self.course,self.class_name=auto,course,class_name
        auto.SetGlobalSearchTimeout(.5)
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.windll.kernel32
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
        windows=[]
        for control in auto.GetRootControl().GetChildren():
            try:
                if control.ClassName!='Chrome_WidgetWin_1':continue
                handle=kernel.OpenProcess(0x1000,False,control.ProcessId)
                if not handle:continue
                try:
                    name=ctypes.create_unicode_buffer(32768);size=wintypes.DWORD(32768)
                    if not kernel.QueryFullProcessImageNameW(handle,0,name,ctypes.byref(size)) or Path(name.value).name.casefold()!='msedge.exe':continue
                finally:kernel.CloseHandle(handle)
                tabs=[c for c in self.walk(control,depth=18) if c.ControlTypeName=='TabItemControl' and re.fullmatch(re.escape(course)+r'(?: - 内存使用率.*)?',c.Name or '')]
                if len(tabs)==1:windows.append((control,tabs[0]))
            except (OSError,ValueError):continue
        if len(windows)!=1:raise CaptureStopped('请在Edge保留一个目标高数课程页，再启动一次批量读取。')
        self.window,self.course_tab=windows[0]
        self.actions=[];self.deadline=time.monotonic()+300

    def bound(self):
        if time.monotonic()>self.deadline:raise CaptureStopped('本次读取已到时间，已读原件保留；没有继续操作平台。')

    def wait_foreground(self,seconds=120,progress=None):
        import ctypes
        from ctypes import wintypes
        user=ctypes.windll.user32;user.GetForegroundWindow.restype=wintypes.HWND
        end=time.monotonic()+seconds
        while int(user.GetForegroundWindow() or 0)!=self.window.NativeWindowHandle:
            if time.monotonic()>=end:raise CaptureStopped('等待学习通窗口超时。请保留课程结果页在前台后再启动一次读取。')
            if progress:progress('waiting_for_browser','请将现有学习通窗口放到前台；接下来会整批读取，无需逐题切换。')
            time.sleep(1);self.bound()

    def wait_for(self,read,seconds=15):
        end=time.monotonic()+seconds
        while True:
            self.bound()
            try:
                result=read()
                if result:return result
            except CaptureStopped:pass
            if time.monotonic()>=end:raise CaptureStopped('结果页还未就绪，已有读取结果保留；没有重试提交或学习操作。')
            time.sleep(.3)

    def walk(self,root,depth=24,maximum=4000):
        stack=[(root,0)];count=0
        while stack and count<maximum:
            control,level=stack.pop();count+=1;yield control
            if level>=depth:continue
            try:
                if control.ControlTypeName=='EditControl' or control.IsPassword:continue
                stack.extend((c,level+1) for c in reversed(control.GetChildren()))
            except Exception:continue

    def location(self):
        for node in self.walk(self.window,depth=18):
            if node.ControlTypeName=='EditControl' and not node.IsPassword and re.search(r'地址|Address',node.Name or ''):
                pattern=node.GetValuePattern()
                if pattern:return safe_location(pattern.Value)
        raise CaptureStopped('无法核实当前页面地址，未操作学习通。')

    def select_course(self):
        tabs=[c for c in self.walk(self.window,depth=18) if c.ControlTypeName=='TabItemControl' and re.fullmatch(re.escape(self.course)+r'(?: - 内存使用率.*)?',c.Name or '')]
        if len(tabs)!=1:raise CaptureStopped('目标课程标签不唯一，未继续读取。')
        self.course_tab=tabs[0]
        pattern=self.course_tab.GetSelectionItemPattern()
        if not pattern:raise CaptureStopped('当前浏览器不支持安全切换课程页。')
        pattern.Select(waitTime=.3)
        self.location()
        return self.document(self.course)

    def document(self,name):
        docs=[c for c in self.walk(self.window,depth=28) if c.ControlTypeName=='DocumentControl' and c.Name==name]
        if len(docs)!=1:raise CaptureStopped('课程结果页尚未就绪，请保留课程页后重试。')
        self.location();return docs[0]

    def invoke(self,control,operation,labels=None,day=None):
        self.bound();self.location()
        name=(control.Name or '').strip();kind=control.ControlTypeName
        if operation=='question_navigation' and not name:
            child_text=self.text(control)
            if len(child_text)==1 and child_text[0].isdigit():name=child_text[0]
        if operation=='menu':allowed=name in {'任务','学习记录'} and kind=='HyperlinkControl'
        elif operation=='ended_exercise':allowed=bool(labels and day and is_ended_exercise(labels,day)) and kind in {'ListItemControl','GroupControl','PaneControl','ButtonControl','HyperlinkControl'}
        elif operation=='question_navigation':
            allowed=bool(labels and submitted_result(labels)) and name.isdigit() and kind in {'ListItemControl','ButtonControl','HyperlinkControl','GroupControl'}
            if allowed:
                allowed=any(control.GetRuntimeId()==target.GetRuntimeId() for _,target in self.question_navigation(self.document(self.course)))
        elif operation=='report':allowed=name=='详情' and kind=='HyperlinkControl' and labels in {'章节任务点','章节测验'}
        elif operation=='graded_quiz':allowed=name=='查看' and kind=='HyperlinkControl' and any('已完成' in s for s in (labels or []))
        else:allowed=False
        if not allowed:raise CaptureStopped('这个控件不是已核实的只读记录导航，未操作。')
        getter=getattr(control,'GetInvokePattern',None)
        pattern=getter() if getter else None
        if pattern:pattern.Invoke(waitTime=.3)
        else:
            legacy=control.GetLegacyIAccessiblePattern()
            if not legacy or not legacy.DefaultAction:raise CaptureStopped('结果页不支持安全导航，未使用鼠标或答题控件。')
            legacy.DoDefaultAction(waitTime=.3)
        self.actions.append({'operation':operation,'target':safe_text(name) if name else 'verified_ended_activity'})

    def menu(self,name):
        doc=self.select_course()
        links=[]
        for control in self.walk(doc):
            if control.ControlTypeName!='HyperlinkControl' or control.Name!=name:continue
            parent=control.GetParentControl()
            navigation=parent.GetParentControl() if parent else None
            names={c.Name for c in navigation.GetChildren() if c.ControlTypeName=='ListItemControl'} if navigation else set()
            if {'任务','章节','学习记录'}<=names:links.append(control)
        if len(links)!=1:raise CaptureStopped('课程记录菜单不唯一，未继续操作。')
        self.invoke(links[0],'menu');return self.document(self.course)

    def task_cards(self,day):
        doc=self.menu('任务')
        def ready():
            labels=self.text(doc)
            return labels if self.class_name in labels and any('已结束' in s for s in labels) else None
        self.wait_for(ready)
        ended=[];empty=False
        for node in self.walk(doc):
            if node.ControlTypeName!='TextControl' or (node.Name or '').strip()!='已结束':continue
            parent=node
            for _ in range(4):
                parent=parent.GetParentControl()
                if not parent:break
                labels=self.text(parent)
                if any('进行中' in s or '结束时间' in s for s in labels):break
                if any(re.fullmatch(r'[（(]?\s*0\s*[）)]?',s) for s in labels):empty=True
                sibling=parent.GetNextSiblingControl()
                if sibling and any(s.startswith('结束时间') for s in self.text(sibling)):ended.append(sibling);break
        if not ended:
            if empty:return []
            raise CaptureStopped('未能可靠识别已结束列表，未进入任何活动。')
        if len(ended)!=1:raise CaptureStopped('已结束列表不唯一，未进入活动。')
        cards=[];seen=set()
        for node in self.walk(ended[0]):
            try:
                if node.ControlTypeName!='TextControl' or not re.match(r'结束时间[：:]\s*'+re.escape(day[5:]),node.Name or ''):continue
                parent=node
                for _ in range(6):
                    parent=parent.GetParentControl()
                    if not parent:break
                    labels=self.text(parent);all_names=[safe_text(c.Name) for c in self.walk(parent) if c.Name and c.ControlTypeName!='EditControl']
                    ends=re.findall(r'结束时间[：:]\s*(\d\d-\d\d\s+\d\d:\d\d)','\n'.join(labels))
                    if len(set(ends))>1:break
                    if is_ended_exercise(all_names,day) and self.action(parent):
                        identity=ends[0]
                        if identity not in seen:cards.append((identity,parent,all_names));seen.add(identity)
                        break
            except CaptureStopped:raise
            except Exception:continue
        return cards

    def result_document(self,name=None):
        names={name} if name else {self.course,'查看已批阅作业'}
        def ready():
            docs=[c for c in self.walk(self.window,depth=30) if c.ControlTypeName=='DocumentControl' and c.Name in names]
            for doc in reversed(docs):
                labels=self.text(doc)
                if submitted_result(labels) or (name=='查看已批阅作业' and any('本次成绩' in s for s in labels)):return doc
            return None
        return self.wait_for(ready)

    def snapshot(self,document):
        labels=self.text(document)
        if document.Name==self.course and '课程图谱' in labels:labels=labels[labels.index('课程图谱')+1:]
        return {'source_url':self.location(),'scope_title':safe_text(document.Name),'text':labels}

    def question_navigation(self,document):
        labels=self.text(document)
        if not submitted_result(labels):return []
        for parent in self.walk(document):
            if parent.ControlTypeName=='TextControl' and re.match(r'^[【\[]\s*(?:单选题|多选题|判断题)',(parent.Name or '').strip()):break
            try:
                children=parent.GetChildren()
                if not 2<=len(children)<=30:continue
                values=[]
                for child in children:
                    texts=self.text(child)
                    name=(child.Name or '').strip()
                    if not texts and name:texts=[name]
                    if len(texts)!=1 or not texts[0].isdigit():break
                    if child.ControlTypeName not in {'ListItemControl','ButtonControl','HyperlinkControl','GroupControl'}:break
                    values.append((int(texts[0]),child))
                if len(values)==len(children) and [n for n,_ in values]==list(range(1,len(values)+1)):
                    return values
            except Exception:continue
        return []

    def classroom(self,day,on_record=None):
        identities=[identity for identity,_,_ in self.task_cards(day)][:6]
        records=[]
        for identity in identities:
            cards=[row for row in self.task_cards(day) if row[0]==identity]
            if len(cards)!=1:raise CaptureStopped('已结束练习的身份发生变化，未继续打开。')
            _,control,proof=cards[0];self.invoke(control,'ended_exercise',proof,day)
            document=self.result_document();first=self.snapshot(document)
            pages=[first];questions=question_blocks(first['text']);navigation=self.question_navigation(document)
            record={'activity_type':'classroom','activity_name':'随堂练习','activity_day':day,'ended_label':identity,'pages':pages}
            if on_record:on_record(record)
            if len(questions)==1 and len(navigation)>1:
                for number,_ in navigation:
                    if number==questions[0]['number']:continue
                    fresh=self.question_navigation(document);target=next((c for n,c in fresh if n==number),None)
                    if not target:raise CaptureStopped('题号导航已变化，未操作。')
                    self.invoke(target,'question_navigation',first['text'])
                    self.wait_for(lambda:any(q['number']==number for q in question_blocks(self.text(document))))
                    pages.append(self.snapshot(document))
                    if on_record:on_record(record)
            records.append(record)
        return records

    def open_report(self,section):
        self.menu('学习记录')
        def report_container():
            docs=[c for c in self.walk(self.window,depth=30) if c.ControlTypeName=='DocumentControl' and c.Name=='学习记录']
            return docs[0] if len(docs)==1 else None
        doc=self.wait_for(report_container)
        elements=list(self.walk(doc));links=[]
        for node in elements:
            if node.ControlTypeName=='ListItemControl' and section in (node.Name or ''):
                choices=[c for c in self.walk(node) if c.ControlTypeName=='HyperlinkControl' and c.Name=='详情']
                if len(choices)==1:links=choices;break
        if not links and section=='章节任务点':
            start=next((i for i,c in enumerate(elements) if c.ControlTypeName=='TextControl' and c.Name=='章节任务点'),None)
            end=next((i for i,c in enumerate(elements) if start is not None and i>start and c.ControlTypeName=='TextControl' and c.Name=='在线学习'),None)
            if start is not None and end is not None:links=[c for c in elements[start:end] if c.ControlTypeName=='HyperlinkControl' and c.Name=='详情']
        if len(links)!=1:raise CaptureStopped('统计结果入口不唯一，未继续读取。')
        self.invoke(links[0],'report',section)
        name='统计-在线学习' if section=='章节任务点' else '统计-章节测验'
        def loaded():
            doc=self.document(name);labels=self.text(doc)
            required='章节测验名称' if section=='章节测验' else '章节任务点'
            return doc if any(required in label for label in labels) and (section!='章节测验' or any('所属章节' in label for label in labels)) else None
        return self.wait_for(loaded)

    def preview(self):
        material_doc=self.open_report('章节任务点')
        materials=self.snapshot(material_doc)
        for control in self.walk(material_doc):
            try:
                pattern=control.GetPattern(self.auto.PatternId.ScrollPattern)
                if pattern and pattern.VerticallyScrollable:pattern.SetScrollPercent(-1,100,waitTime=.2);break
            except Exception:continue
        tail=self.snapshot(material_doc)
        if tail['text']!=materials['text']:materials['text'].extend(tail['text'])
        quiz_doc=self.open_report('章节测验');choices=[];seen=set()
        for node in self.walk(quiz_doc):
            if node.ControlTypeName!='HyperlinkControl' or node.Name!='查看':continue
            parent=node
            for _ in range(6):
                parent=parent.GetParentControl()
                if not parent:break
                labels=self.text(parent)
                links=[c for c in self.walk(parent) if c.ControlTypeName=='HyperlinkControl' and c.Name=='查看']
                if len(links)>1:break
                if len(links)==1 and any('已完成' in s for s in labels):
                    title=next((s for s in labels if s and s!='查看' and not re.match(r'已完成|[\d.]+$|[-—]$',s)),None)
                    chapter=next((re.search(r'\d+(?:\.\d+)+',s)[0] for s in labels if re.search(r'\d+(?:\.\d+)+[、.，]',s)),None)
                    if title and (title,chapter) not in seen:choices.append((tuple(map(int,chapter.split('.'))) if chapter else (len(choices),),title,node,labels,chapter));seen.add((title,chapter))
                    break
        if not choices:return {'activity_type':'preview_material','activity_name':'课前预习材料','pages':[materials]},None
        _,title,node,labels,chapter=max(choices,key=lambda item:item[0]);self.invoke(node,'graded_quiz',labels)
        result=self.result_document('查看已批阅作业')
        return {'activity_type':'preview_material','activity_name':title,'chapter':chapter,'pages':[materials]}, {'activity_type':'preview','activity_name':title,'chapter':chapter,'pages':[self.snapshot(result)]}

    def text(self,root):
        result=[]
        for node in self.walk(root):
            try:
                if node.IsPassword or node.ControlTypeName=='EditControl':continue
                if node.ControlTypeName in {'TextControl','DataItemControl','HeaderItemControl'} and node.Name:
                    if node.ControlTypeName=='TextControl' or not node.GetChildren():result.append(safe_text(node.Name))
            except Exception:continue
        return result

    @staticmethod
    def action(control):
        try:
            pattern=control.GetLegacyIAccessiblePattern()
            return pattern.DefaultAction if pattern else ''
        except Exception:return ''

    def inspect(self):
        doc=self.select_course();rows=[]
        for control in self.walk(doc):
            try:
                if control.IsPassword or control.ControlTypeName=='EditControl':continue
                action=self.action(control)
                if action:rows.append({'type':control.ControlTypeName,'name':safe_text(control.Name),'action':action,'parent':safe_text(control.GetParentControl().Name)})
            except Exception:continue
        return {'source_url':self.location(),'title':doc.Name,'text':self.text(doc),'actions':rows}

    def screenshot(self,document,target):
        """Print only the verified Edge document; never copy the desktop/chrome."""
        import ctypes
        from ctypes import wintypes
        from PIL import Image
        before=self.location()
        if document.Name not in {self.course,'查看已批阅作业','统计-在线学习','统计-章节测验'}:raise CaptureStopped('当前页面不在只读结果范围内。')
        for node in self.walk(document):
            if node.IsPassword:raise CaptureStopped('当前页面需要重新登录，未保存图像。')
        user,gdi=ctypes.windll.user32,ctypes.windll.gdi32
        user.SetThreadDpiAwarenessContext.argtypes=[wintypes.HANDLE];user.SetThreadDpiAwarenessContext.restype=wintypes.HANDLE
        user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        class Rect(ctypes.Structure):_fields_=[('left',wintypes.LONG),('top',wintypes.LONG),('right',wintypes.LONG),('bottom',wintypes.LONG)]
        class Header(ctypes.Structure):
            _fields_=[('size',wintypes.DWORD),('width',wintypes.LONG),('height',wintypes.LONG),('planes',wintypes.WORD),('bits',wintypes.WORD),('compression',wintypes.DWORD),('image_size',wintypes.DWORD),('xp',wintypes.LONG),('yp',wintypes.LONG),('colors',wintypes.DWORD),('important',wintypes.DWORD)]
        user.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(Rect)]
        user.GetWindowDC.argtypes=[wintypes.HWND];user.GetWindowDC.restype=wintypes.HDC
        user.ReleaseDC.argtypes=[wintypes.HWND,wintypes.HDC]
        user.PrintWindow.argtypes=[wintypes.HWND,wintypes.HDC,wintypes.UINT]
        gdi.CreateCompatibleDC.argtypes=[wintypes.HDC];gdi.CreateCompatibleDC.restype=wintypes.HDC
        gdi.CreateCompatibleBitmap.argtypes=[wintypes.HDC,ctypes.c_int,ctypes.c_int];gdi.CreateCompatibleBitmap.restype=wintypes.HBITMAP
        gdi.SelectObject.argtypes=[wintypes.HDC,wintypes.HANDLE];gdi.SelectObject.restype=wintypes.HANDLE
        gdi.GetDIBits.argtypes=[wintypes.HDC,wintypes.HBITMAP,wintypes.UINT,wintypes.UINT,ctypes.c_void_p,ctypes.POINTER(Header),wintypes.UINT]
        gdi.DeleteObject.argtypes=[wintypes.HANDLE];gdi.DeleteDC.argtypes=[wintypes.HDC]
        hwnd=self.window.NativeWindowHandle;rect=Rect()
        if not user.GetWindowRect(hwnd,ctypes.byref(rect)):raise CaptureStopped('无法读取课程窗口范围。')
        width,height=rect.right-rect.left,rect.bottom-rect.top
        if width<200 or height<200 or width*height>20_000_000:raise CaptureStopped('课程窗口尺寸不适合读取。')
        source=user.GetWindowDC(hwnd);memory=gdi.CreateCompatibleDC(source);bitmap=gdi.CreateCompatibleBitmap(source,width,height);old=gdi.SelectObject(memory,bitmap)
        try:
            if not user.PrintWindow(hwnd,memory,2):raise CaptureStopped('课程窗口暂不可读取。')
            header=Header(ctypes.sizeof(Header),width,-height,1,32,0,width*height*4,0,0,0,0)
            pixels=ctypes.create_string_buffer(width*height*4)
            if not gdi.GetDIBits(memory,bitmap,0,height,pixels,ctypes.byref(header),0):raise CaptureStopped('课程页面图像未就绪。')
            view=document.BoundingRectangle;chrome_bottom=rect.top
            for node in self.walk(self.window,depth=18):
                if node.ControlTypeName=='EditControl' and not node.IsPassword and re.search(r'地址|Address',node.Name or ''):chrome_bottom=max(chrome_bottom,node.BoundingRectangle.bottom+40)
            crop=(max(0,int(view.left-rect.left)),max(0,int(max(view.top,chrome_bottom)-rect.top)),min(width,int(view.right-rect.left)),min(height,int(view.bottom-rect.top)))
            if crop[2]<=crop[0] or crop[3]<=crop[1] or self.location()!=before:raise CaptureStopped('结果页面在读取时发生变化，未保存图像。')
            image=Image.frombuffer('RGB',(width,height),pixels.raw,'raw','BGRX',0,1).crop(crop)
            if all(high-low<4 for low,high in image.getextrema()):raise CaptureStopped('浏览器没有提供可读页面图像，请在读取期间保留结果窗口可见。')
            image.save(target,format='PNG')
            return {'file':Path(target).name,'width':image.width,'height':image.height,'viewport':[rect.left+crop[0],rect.top+crop[1],rect.left+crop[2],rect.top+crop[3]]}
        finally:
            gdi.SelectObject(memory,old);gdi.DeleteObject(bitmap);gdi.DeleteDC(memory);user.ReleaseDC(hwnd,source)

    def preview_images(self,record,root):
        from PIL import Image
        import hashlib
        document=self.result_document('查看已批阅作业');images=[];option_images=[];seen=set();warnings=[]
        scroller=None
        for node in self.walk(document):
            try:
                pattern=node.GetPattern(self.auto.PatternId.ScrollPattern)
                if pattern and pattern.VerticallyScrollable:scroller=pattern;break
            except Exception:continue
        for percent in ([0,50,100] if scroller else [0]):
            self.bound()
            if scroller:scroller.SetScrollPercent(-1,percent,waitTime=.25)
            path=root/('preview-view-'+str(percent)+'.png')
            try:frame=self.screenshot(document,path)
            except CaptureStopped as error:warnings.append(str(error));continue
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in seen:path.unlink();continue
            seen.add(digest);frame['sha256']=digest;images.append(frame)
            image=Image.open(path)
            # Native labels bind each crop to one question and letter; OCR is
            # later performed locally and never supplies grading decisions.
            containers=[]
            for node in self.walk(document):
                if node.ControlTypeName!='TextControl' or not re.search(r'[【\[]\s*(单选题|多选题)',node.Name or ''):continue
                parent=node
                for _ in range(7):
                    parent=parent.GetParentControl()
                    if not parent:break
                    lines=self.text(parent)
                    blocks=question_blocks(lines)
                    if len(blocks)>1:break
                    if len(blocks)==1 and any(s.rstrip('：:')=='我的答案' for s in lines):containers.append((parent,blocks[0]));break
            for container,q in containers:
                for label in self.walk(container):
                    match=re.fullmatch(r'([A-D])[.、．]',(label.Name or '').strip()) if label.ControlTypeName=='TextControl' else None
                    if not match:continue
                    letter=match[1]
                    if any(o['number']==q['number'] and o['letter']==letter for o in option_images):continue
                    parent=label
                    for _ in range(3):
                        parent=parent.GetParentControl()
                        if not parent:break
                        candidates=[c for c in self.walk(parent,depth=5) if c.ControlTypeName=='ImageControl']
                        if len(candidates)>1:break
                        if len(candidates)!=1:continue
                        rect=candidates[0].BoundingRectangle;left,top,right,bottom=frame['viewport']
                        if rect.width()<=0 or rect.height()<=0 or rect.left<left or rect.top<top or rect.right>right or rect.bottom>bottom:break
                        crop=image.crop((int(rect.left-left),int(rect.top-top),int(rect.right-left),int(rect.bottom-top)))
                        name=f'preview-question-{q["number"]}-{letter}.png';crop.save(root/name,format='PNG')
                        option_images.append({'number':q['number'],'question':q['question'],'letter':letter,'file':name,'sha256':hashlib.sha256((root/name).read_bytes()).hexdigest()})
                        break
        record.update(images=images,option_images=option_images,image_warnings=list(dict.fromkeys(warnings)))

    def batch(self,day,root,progress,pending_days=()):
        self.wait_foreground(progress=progress);self.select_course()
        progress('running','正在读取本次已结束的课堂练习。')
        partial=[]
        def save_record(record):
            identity=(record['activity_type'],record.get('ended_label'),record['activity_name'])
            prior=next((i for i,r in enumerate(partial) if (r['activity_type'],r.get('ended_label'),r['activity_name'])==identity),None)
            if prior is None:partial.append(record)
            else:partial[prior]=record
            (root/'partial.json').write_text(json.dumps({'schema':'readonly-native-batch-v1','requested_cycle_date':day,'native_course_name':self.course,'native_class_name':self.class_name,'records':partial,'partial':True},ensure_ascii=False),encoding='utf-8')
        records=[]
        for source_day in dict.fromkeys([day,*pending_days]):records.extend(self.classroom(source_day,on_record=save_record))
        progress('running','正在读取预习完成记录和已批阅习题。')
        material,preview=self.preview();records.append(material);save_record(material)
        if preview:
            progress('running','正在读取整页图片选项，不操作答题。')
            self.preview_images(preview,root);records.append(preview)
            save_record(preview)
        result={'schema':'readonly-native-batch-v1','native_course_name':self.course,'native_class_name':self.class_name,
            'requested_cycle_date':day,'collected_at':datetime.now().astimezone().isoformat(),'records':records,'navigation':self.actions,
            'credentials_collected':False,'platform_writes':False}
        progress('captured','平台读取完成，正在保存和整理本次记录。')
        return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--probe',action='store_true');parser.add_argument('--batch',action='store_true');parser.add_argument('--day');parser.add_argument('--pending-day',action='append',default=[]);parser.add_argument('--image',action='store_true');parser.add_argument('--output',required=True)
    args=parser.parse_args()
    target=Path(args.output).resolve();target.parent.mkdir(parents=True,exist_ok=True)
    def progress(status,message):
        path=target.with_name('progress.json');temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'status':status,'message':message},ensure_ascii=False),encoding='utf-8');temporary.replace(path)
    try:
        reader=EdgeReader()
        if args.batch:
            if not args.day or not re.fullmatch(r'20\d\d-\d\d-\d\d',args.day):raise CaptureStopped('本次学习日期无效，未读取平台。')
            from datetime import date
            pending=[]
            for value in args.pending_day[:2]:
                try:
                    if not 0<=(date.fromisoformat(args.day)-date.fromisoformat(value)).days<=14:raise ValueError()
                except ValueError:raise CaptureStopped('待公布结果的日期不在本次只读范围内。')
                pending.append(value)
            result=reader.batch(args.day,target.parent,progress,pending)
        else:result=reader.inspect()
    except CaptureStopped as error:
        progress('needs_attention',str(error));print(json.dumps({'status':'needs_attention','message':str(error)},ensure_ascii=False));return
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.image:reader.screenshot(reader.document(reader.course),target.with_suffix('.png'))
    print(json.dumps({'saved':True,'record_count':len(result.get('records',[])),'text_count':len(result.get('text',[]))}))
if __name__=='__main__':main()
