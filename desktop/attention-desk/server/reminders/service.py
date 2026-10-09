"""Lightweight local scheduler with bounded delivery and clean lifespan ownership."""
from __future__ import annotations

import asyncio
import logging
import os
import threading

from ..planning.time import utc_now
from .store import ReminderStore
from .windows import WindowsNotifier, content

LOG = logging.getLogger(__name__)


class ReminderService:
    def __init__(self,store,notifier=None,*,clock=utc_now,poll_seconds=None):
        self.repository = ReminderStore(store)
        self.notifier = notifier or WindowsNotifier()
        self.clock = clock
        self.poll_seconds = max(0,float(os.environ.get('ATTENTION_REMINDER_POLL','30') if poll_seconds is None else poll_seconds))
        self._stop = asyncio.Event()
        self._task = None
        self._tick_lock = threading.Lock()
        self.last_result = None

    async def start(self):
        await asyncio.to_thread(self.repository.recover_interrupted,at=self.clock())
        if self.poll_seconds and self._task is None:
            self._task = asyncio.create_task(self._run(),name='attention-reminders')

    def tick(self):
        if not self._tick_lock.acquire(blocking=False):
            return 0
        try:
            at = self.clock()
            self.repository.reconcile(at=at)
            rows = self.repository.claim(at=at)
            settings = self.repository.settings()
            for row in rows:
                item = self.repository.notification_data(row)
                if not item:
                    result = 'delivery_failed'
                else:
                    try:
                        result = self.notifier.send(content(item,settings['privacy']),
                            delivery_id=f"{row['id']}:{row['generation']}",event_id=row['event_id'],
                            on_failed=lambda result,current=row:self.repository.finish(current,result,at=self.clock()))
                    except Exception:
                        result = 'backend_unavailable'
                self.repository.finish(row,result,at=self.clock())
                self.last_result = result
            return len(rows)
        finally:
            self._tick_lock.release()

    async def _run(self):
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                LOG.error('reminder_scheduler result=cycle_failed')
                self.last_result = 'cycle_failed'
            try:
                await asyncio.wait_for(self._stop.wait(),timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def close(self):
        self._stop.set()
        if self._task:
            await self._task
            self._task = None

    def test_notification(self):
        try:
            result = self.notifier.test()
        except Exception:
            result = 'backend_unavailable'
        self.last_result = result
        messages = {
            'os_accepted':'Windows 已接受测试通知；是否出现横幅请查看系统通知设置。',
            'os_blocked':'Windows 当前关闭或限制了用户通知。请在 Windows 通知设置中手动检查后重试；已有事项仍可在提醒中心查看。',
            'unsupported':'当前系统不支持 Windows 通知；已有事项仍可在提醒中心查看。',
            'backend_unavailable':'Windows 通知组件不可用，请核对依赖安装；已有事项仍可在提醒中心查看。',
        }
        return {'result':result,'accepted':result=='os_accepted',
                'notification_setting':getattr(self.notifier,'last_setting',None),
                'message':messages.get(result,'系统通知未送达；已有事项仍可在提醒中心查看。')}
