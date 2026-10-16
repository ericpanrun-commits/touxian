"""Production sends require an explicit Actions context plus verified remote state."""
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from ..runtime import flag

REPOSITORY = 'sssssjw11/touxian'


@dataclass(frozen=True)
class ActionsRuntime:
    environment: str = 'development'
    role: str = 'actions'
    instance_id: str = 'github-actions'
    auto_sync: bool = False
    notification_authority: bool = False
    dry_run: bool = True
    execution_verified: bool = False
    storage_verified: bool = False
    database_url: str = field(default='',repr=False)
    expected_database_id: str = ''
    run_id: str = ''
    bootstrap: bool = False
    poll_seconds: int = 3600

    @classmethod
    def from_env(cls, values):
        expected = values.get('ZQU_EXPECTED_REPOSITORY',REPOSITORY)
        verified = (expected == REPOSITORY and values.get('GITHUB_ACTIONS') == 'true'
            and values.get('GITHUB_REPOSITORY') == expected
            and values.get('GITHUB_EVENT_NAME') in {'schedule','workflow_dispatch'}
            and values.get('GITHUB_REF') == 'refs/heads/main')
        database = values.get('SUPABASE_DB_URL','')
        if database:
            try:
                address = urlsplit(database)
                if address.scheme not in {'postgres','postgresql'} or not address.hostname or not address.password or address.fragment:
                    raise ValueError()
                if not (address.hostname.endswith('.pooler.supabase.com') or address.hostname.endswith('.supabase.co')):
                    raise ValueError()
                address.port
            except ValueError:
                raise ValueError('SUPABASE_DB_URL 必须是 Supabase 的安全 PostgreSQL 连接配置。') from None
        mode = values.get('APP_ENV','development')
        if mode not in {'production','development'}:
            raise ValueError('APP_ENV 无效。')
        return cls(environment=mode,auto_sync=flag(values,'AUTO_SYNC_ENABLED'),
            notification_authority=flag(values,'PRODUCTION_NOTIFICATION_AUTHORITY'),
            dry_run=flag(values,'NOTIFICATION_DRY_RUN',True),execution_verified=verified,
            database_url=database,expected_database_id=values.get('PRODUCTION_DATABASE_ID',''),
            run_id=values.get('GITHUB_RUN_ID','')+':'+values.get('GITHUB_RUN_ATTEMPT','1'))

    @property
    def automatic_sync_allowed(self):
        return self.environment=='production' and self.execution_verified and self.storage_verified and self.auto_sync

    @property
    def automatic_bark_allowed(self):
        return self.automatic_sync_allowed and self.notification_authority and not self.dry_run and not self.bootstrap

    @property
    def preview_allowed(self):
        return self.automatic_sync_allowed and self.dry_run

    @property
    def windows_allowed(self):
        return False

    def public(self):
        return {'environment':self.environment,'runtime':'actions','instance':self.instance_id,
            'mode':'scheduled','automatic_sync':self.automatic_sync_allowed,'automatic_bark':self.automatic_bark_allowed,
            'production_authority':self.automatic_bark_allowed,'dry_run':self.dry_run,'bootstrap':self.bootstrap,
            'database_persistent':self.storage_verified,'cloud_deployed':False}
