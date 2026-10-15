"""One official dispatcher: existing Windows delivery plus a durable Bark outbox."""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timezone

from ..official_rules import load_profile
from ..official_store import OfficialStore
from ..planning.time import utc_now
from .bark import BarkClient, DeliveryResult
from .config import BarkConfig
from .policy import presentation
from .store import NotificationStore

LOG = logging.getLogger(__name__)


class NotificationDispatcher:
    def __init__(self, personal, *, config=None, client=None, notifier=None, clock=utc_now):
        self.config = config or BarkConfig.from_env()
        self.client = client or BarkClient(self.config)
        self.repository = NotificationStore(personal)
        self.official = OfficialStore(personal)
        self.notifier, self.clock = notifier, clock
        self._lock = threading.Lock()
        try:
            self.repository.activate(self.config, at=self.clock())
        except Exception:
            LOG.error('notification_activation channel=bark result=deferred')

    def tick(self, *, limit=10):
        if not self._lock.acquire(blocking=False):
            return 0
        count = 0
        try:
            self.repository.recover(at=self.clock())
            self.repository.reconcile(self.config, at=self.clock())
            for _ in range(limit):
                row = self.repository.claim(self.config, at=self.clock())
                if not row:
                    break
                payload = presentation(row['item'], row['sources'], row['policy'], self.config, at=self.clock(), notification_id=row['id'])
                if not self.repository.start_request(row, at=self.clock()):
                    continue
                try:
                    result = self.client.send(payload, max_attempts=1)
                except Exception:
                    result = DeliveryResult(error='delivery_unknown')
                self.repository.finish(row, result, at=self.clock())
                count += 1
        except Exception:
            # Neither provider failures nor local queue failures may fail ingestion/scheduling.
            LOG.error('notification_dispatch channel=bark result=cycle_failed')
        finally:
            self._lock.release()
        return count

    def settings(self):
        prefs = self.repository.preferences()
        state = 'unconfigured' if not self.config.configured else 'disabled' if not self.config.enabled or not prefs['enabled'] else (
            'failed' if prefs['last_error'] else 'healthy' if prefs['last_success_at'] else 'configured')
        return {**self.config.public(), **prefs, 'status': state,
                'preference_enabled': prefs['enabled'], 'enabled': self.config.enabled and prefs['enabled'], 'backend_required': True}

    def save_settings(self, payload):
        self.repository.save_preferences(payload.enabled, payload.policy, payload.expected_version)
        self.repository.activate(self.config, at=self.clock())
        return self.settings()

    def test_notification(self, *, body=None):
        prefs = self.repository.preferences()
        error = self.config.error or ('not_configured' if not self.config.configured else 'disabled' if not self.config.enabled or not prefs['enabled'] else None)
        if error:
            return DeliveryResult(error=error).public()
        if not self.repository.claim_test(at=self.clock()):
            return DeliveryResult(error='test_cooldown').public()
        from ..personal_store import identifier
        try:
            kwargs = {'notification_id': 'test:' + identifier()}
            if body is not None:
                kwargs['body'] = body
            result = self.client.test(**kwargs)
        except Exception:
            result = DeliveryResult(error='delivery_unknown')
        self.repository.finish_test(result, at=self.clock())
        return result.public()

    def notify_windows(self, raw_id):
        """Phase 7 Windows policy/privacy and stable claim semantics are preserved."""
        try:
            item = self.official.detail(raw_id)
            profile = load_profile()
            if not (item['importance'] == 'critical' or item['importance'] == 'high' and item['relevance'] >= profile['notify_relevance']):
                return
            publication = item['published_at']
            if not publication or not (0 <= (datetime.now(timezone.utc).date() - date.fromisoformat(publication)).days <= profile['notify_fresh_days']):
                return
            with self.official.personal.connection() as c:
                settings = c.execute('SELECT enabled,privacy FROM reminder_settings WHERE id=1').fetchone()
                if settings and not settings['enabled']:
                    return
                privacy = settings['privacy'] if settings else 'title'
            claimed = self.official.claim_notification(raw_id, profile['notify_max_per_run'])
            if not claimed:
                return
            if self.notifier is None:
                from ..reminders.windows import WindowsNotifier
                self.notifier = WindowsNotifier()
            text = ['Attention Desk', '新重要官网通知' if privacy == 'hidden' else item['title'], '请在官网信息工作台核对。']
            result = self.notifier.send(text, delivery_id='official:' + claimed['display_group'])
            self.official.finish_notification(claimed['display_group'], result)
        except Exception:
            return
