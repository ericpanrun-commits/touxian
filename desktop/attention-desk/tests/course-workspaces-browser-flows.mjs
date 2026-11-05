import {expect} from 'playwright/test';
import assert from 'node:assert/strict';

export async function courseNoteConfirmationFlow({phone,BASE,state,course,shot,reports}) {
  await phone.page.goto(BASE+'/?view=learning&id=capture-course-confirmation');
  const sheet=phone.page.getByRole('dialog',{name:'继续整理课堂笔记',exact:true});
  await expect(sheet).toBeVisible();
  await sheet.getByLabel('这份材料属于哪门课',{exact:true}).selectOption(course.id);
  const lectures=sheet.getByLabel('已有课次（可直接选择）',{exact:true});
  await expect(lectures.locator('option[value="lecture-computing"]')).toHaveCount(1,{timeout:8000});
  await expect(lectures.locator('option[value="lecture-synthetic"]')).toHaveCount(0);
  await lectures.selectOption('lecture-computing');
  await expect(sheet.getByLabel('课堂日期（补充或纠正时填写）',{exact:true})).toHaveValue('2026-10-29');
  await shot(phone.page,'phone-course-confirmation.png');
  await sheet.getByRole('button',{name:'继续自动整理',exact:true}).click();
  const path='/learning/captures/capture-course-confirmation/confirm';
  await expect.poll(()=>[...state.commands.values()].find(item=>item.path===path),{timeout:20000}).toBeTruthy();
  const command=[...state.commands.values()].find(item=>item.path===path);
  assert.deepEqual(command.body,{course_id:course.id,lecture_id:'lecture-computing',lecture_date:'2026-10-29'});
  assert.equal(command.expected_revision,1);
  await phone.page.goto(BASE+'/?view=today');
  await expect(phone.page.getByRole('heading',{name:'Today',exact:true})).toBeVisible();
  reports.push({course_note_confirmation:'Choosing a general course reads its existing classes, carries the actual date and saves one revision-bound confirmation without borrowing a Math class'});
}

// These clients use the real built UI and independent browser stores. The
// processor below is an isolated API fixture; the actual processor, SQL
// receipts and course ownership are covered by the backend integration tests.
export async function courseWorkspaceFlows({desktop,tablet,phone,BASE,state,books,course,otherCourse,document,dto,changed,shot,reports}) {
  const pulse=async env=>env.page.evaluate(()=>window.dispatchEvent(new Event('online')));
  const completed=(command,result)=>{command.state='completed';command.result=result;command.revision=++state.revision;changed('command',command.id);};
  const lastCommand=path=>[...state.commands.values()].filter(item=>item.path===path).at(-1);
  const localCommands=async page=>page.evaluate(async()=>{
    const request=indexedDB.open('eric-os-v1');
    const db=await new Promise((resolve,reject)=>{request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});
    const get=db.transaction('commands').objectStore('commands').getAll();
    const rows=await new Promise((resolve,reject)=>{get.onsuccess=()=>resolve(get.result);get.onerror=()=>reject(get.error);});db.close();return rows;
  });

  await desktop.page.goto(BASE+'/?view=lecture&id=lecture-computing');
  await expect(desktop.page.getByRole('heading',{name:'隔离测试 · 条件与循环',exact:true})).toBeVisible();
  await expect(desktop.page.getByText('本次复盘仅引用当前提供的笔记，未引用教材。',{exact:true})).toBeVisible();
  await expect(desktop.page.getByText('当前资料没有足够的答案依据，本次未编写自测题。',{exact:true})).toBeVisible();
  await expect(desktop.page.getByRole('button',{name:'生成课堂复盘',exact:true})).toBeEnabled();
  await expect(desktop.page.getByText('高等数学 II',{exact:false})).toHaveCount(0);
  await shot(desktop.page,'desktop-general-notes-only.png');
  const commandCount=state.commands.size;
  await desktop.page.getByRole('button',{name:'生成课堂复盘',exact:true}).click();
  await expect(desktop.page.getByRole('dialog')).toContainText('缺少的教材或课前记录不会补写');
  await desktop.page.getByRole('button',{name:'继续当前操作',exact:true}).click();
  assert.equal(state.commands.size,commandCount,'Cancelling the consent must not start a model request');
  await desktop.page.getByRole('button',{name:'返回课堂',exact:true}).click();
  await expect(desktop.page).toHaveURL(new RegExp('view=course&id='+course.id));
  await expect(desktop.page.getByRole('heading',{name:course.name,exact:true})).toBeVisible();
  await desktop.page.getByRole('button',{name:'上传课堂笔记',exact:true}).click();
  await expect(desktop.page.getByLabel('课程（可选）',{exact:true})).toHaveValue(course.id);
  await expect(desktop.page.getByLabel('保存用途',{exact:true})).toHaveValue('lecture_note');
  await desktop.page.getByLabel('实际课堂日期（可选）',{exact:true}).fill('2026-10-28');
  await desktop.page.getByLabel('内容',{exact:true}).fill('只在隔离环境保存的程序设计笔记。');
  await desktop.setOffline(true);
  await desktop.page.getByRole('button',{name:'保存到 Eric OS',exact:true}).click();
  await expect(desktop.page.getByRole('heading',{name:'最近',exact:true})).toBeVisible();
  await desktop.page.close();await desktop.setOffline(false);desktop.page=await desktop.context.newPage();
  await desktop.page.goto(BASE+'/?view=recent');
  await expect.poll(()=>[...state.captures.values()].find(item=>item.selected_course_id===course.id),{timeout:35000}).toBeTruthy();
  const selectedCapture=[...state.captures.values()].find(item=>item.selected_course_id===course.id);
  assert.equal(selectedCapture.purpose,'lecture_note');assert.match(selectedCapture.text_content,/课堂日期：2026-10-28/);
  assert.notEqual(selectedCapture.shared_at.slice(0,10),'2026-10-28','Upload time stays separate from classroom date');
  reports.push({general_notes:'A sparse notes-only review remains readable without Math material, invented questions or a paid call; course/date choice survives an offline save and closing the tab'});

  await phone.page.goto(BASE+'/?view=textbooks&id='+course.id);
  await expect(phone.page.getByText('这门课尚未登记教材。',{exact:false})).toBeVisible();
  await expect(phone.page.getByText('隔离测试教材',{exact:true})).toHaveCount(0);
  await phone.page.getByRole('button',{name:'登记教材',exact:true}).click();
  await phone.page.getByLabel('教材名称',{exact:true}).fill('隔离测试 · 程序设计原书');
  await phone.page.getByRole('button',{name:'关闭登记',exact:true}).click();
  await expect(phone.page.getByRole('dialog')).toBeVisible();
  await phone.page.getByRole('button',{name:'继续当前操作',exact:true}).click();
  await expect(phone.page.getByLabel('教材名称',{exact:true})).toHaveValue('隔离测试 · 程序设计原书');
  await phone.setOffline(true);
  await phone.page.getByRole('button',{name:'保存教材信息',exact:true}).click();
  await expect(phone.page.getByText('已保存到此设备，联网后自动发送。',{exact:true})).toBeVisible();
  const saved=(await localCommands(phone.page)).find(item=>item.path==='/school/textbooks');
  assert.equal(saved.body.course_id,course.id);assert.equal(saved.resourceKey,'course:'+course.id);
  await phone.page.close();state.lostCommand=true;await phone.setOffline(false);phone.page=await phone.context.newPage();
  await phone.page.goto(BASE+'/?view=textbooks&id='+course.id);
  await expect.poll(()=>lastCommand('/school/textbooks'),{timeout:35000}).toBeTruthy();
  await expect.poll(async()=>(await localCommands(phone.page)).find(item=>item.id===saved.id)?.state,{timeout:35000}).toBe('pending');
  assert.equal([...state.commands.values()].filter(item=>item.path==='/school/textbooks').length,1);
  assert.equal(lastCommand('/school/textbooks').id,saved.id,'A lost receipt must reuse the durable command ID');
  await expect(phone.page.getByText('这门课尚未登记教材。',{exact:false})).toBeVisible();
  await shot(phone.page,'phone-textbook-pending.png');

  const book=dto('textbook','book-computing',{id:'book-computing',...saved.body,version:1,document_id:null,document:null,sections:[],companions:[]},++state.revision);
  books.set(book.id,book);completed(lastCommand('/school/textbooks'),book);changed('textbook',book.id);
  for(const env of [phone,tablet,desktop]){
    await env.page.goto(BASE+'/?view=textbooks&id='+course.id);
    await expect(env.page.locator('.school-book-summary strong').getByText(book.title,{exact:true})).toBeVisible({timeout:20000});
    await expect(env.page.getByText('隔离测试教材',{exact:true})).toHaveCount(0);
  }
  await tablet.page.getByText('关联已上传 PDF',{exact:true}).click();
  await tablet.page.getByLabel('已上传的 PDF',{exact:true}).selectOption('raw-computing-book');
  const checked=tablet.page.getByRole('checkbox',{name:/已核对所选原件/});await checked.check();
  // Another device updates the same canonical textbook while this form stays
  // open. Retain the chosen PDF, require acknowledgement and use the new source.
  book.version=2;book._cloud.revision=++state.revision;changed('textbook',book.id);await pulse(tablet);
  await expect(tablet.page.getByText('教材刚有更新，选择的原件仍保留。',{exact:false})).toBeVisible({timeout:15000});
  await expect(tablet.page.getByLabel('已上传的 PDF',{exact:true})).toHaveValue('raw-computing-book');
  await expect(tablet.page.getByRole('button',{name:'确认关联教材原件',exact:true})).toBeDisabled();
  await tablet.page.getByRole('button',{name:'核对当前教材后继续',exact:true}).click();await expect(checked).not.toBeChecked();await checked.check();
  await tablet.page.getByRole('button',{name:'确认关联教材原件',exact:true}).click();
  await expect.poll(()=>lastCommand('/school/textbook/file'),{timeout:20000}).toBeTruthy();
  const fileCommand=lastCommand('/school/textbook/file');
  assert.equal(fileCommand.body.textbook_id,book.id);assert.equal(fileCommand.body.expected_version,2);
  assert.equal(fileCommand.expected_revision,book._cloud.revision);
  book.document=document;book.document_id=document.id;book.version=3;book._cloud.revision=++state.revision;
  completed(fileCommand,{textbook:book,job:{id:'catalog-computing',status:'completed'}});changed('textbook',book.id);await pulse(tablet);
  await expect(tablet.page.getByRole('heading',{name:book.title+' · 第 1 页',exact:true})).toBeVisible({timeout:20000});
  await tablet.page.getByLabel('PDF 页码',{exact:true}).fill('3');await tablet.page.getByRole('button',{name:'查看这一页',exact:true}).click();
  await expect(tablet.page.locator('canvas[aria-label="原文件第 3 页"]')).toBeVisible({timeout:30000});
  await tablet.page.getByText('章节与本地识别',{exact:true}).click();
  await tablet.page.getByLabel('实际章节 / 小节名称',{exact:true}).fill('隔离测试 · 条件与循环');
  await tablet.page.getByLabel('PDF 起始页',{exact:true}).fill('2');await tablet.page.getByLabel('PDF 结束页',{exact:true}).fill('3');
  book.version=4;book._cloud.revision=++state.revision;changed('textbook',book.id);await pulse(tablet);
  await expect(tablet.page.getByText('教材原件已有更新，已填写的章节仍保留。',{exact:false})).toBeVisible({timeout:15000});
  await expect(tablet.page.getByLabel('实际章节 / 小节名称',{exact:true})).toHaveValue('隔离测试 · 条件与循环');
  await expect(tablet.page.getByRole('button',{name:'保存章节范围',exact:true})).toBeDisabled();
  await tablet.page.getByRole('button',{name:'核对当前原件后继续',exact:true}).click();
  await tablet.page.getByRole('button',{name:'保存章节范围',exact:true}).click();
  await expect.poll(()=>lastCommand('/school/sections'),{timeout:20000}).toBeTruthy();
  const sectionCommand=lastCommand('/school/sections');assert.equal(sectionCommand.body.textbook_id,book.id);assert.equal(sectionCommand.body.expected_version,4);assert.equal(sectionCommand.body.learning_unit_id,null);
  book.sections=[{id:'section-computing',textbook_id:book.id,title:sectionCommand.body.title,start_page:2,end_page:3}];book.version=5;book._cloud.revision=++state.revision;completed(sectionCommand,book.sections[0]);changed('textbook',book.id);await pulse(tablet);
  await expect(tablet.page.getByRole('button',{name:'识别 / 继续这一节',exact:true})).toBeVisible({timeout:20000});
  await shot(tablet.page,'tablet-general-textbook.png');
  const other=dto('textbook','book-history',{...book,id:'book-history',course_id:otherCourse.id,title:'隔离测试 · 历史原书',sections:[]},++state.revision);books.set(other.id,other);changed('textbook',other.id);
  await tablet.page.locator('select[name="textbook_course"]').selectOption(otherCourse.id);
  await expect(tablet.page.getByRole('heading',{name:other.title+' · 第 1 页',exact:true})).toBeVisible();
  await expect(tablet.page.getByLabel('PDF 页码',{exact:true})).toHaveValue('1');
  await expect(tablet.page.getByText('隔离测试 · 条件与循环',{exact:true})).toHaveCount(0);
  reports.push({textbook_workspaces:'Metadata survives offline close and lost acknowledgement with one command; three devices read the same canonical book; file and section changes retain input on concurrent edits; course switch resets the PDF page and never borrows Math material'});
}
