"""Network polling is exercised explicitly, never as a pytest startup side effect."""
import pytest


@pytest.fixture(autouse=True)
def no_unattended_source_polling(monkeypatch):
    monkeypatch.setenv('ATTENTION_OFFICIAL_POLL', '0')
    monkeypatch.setenv('ATTENTION_REMINDER_POLL', '0')
    # Real credentials on the developer machine must never make regression tests send pushes.
    monkeypatch.setenv('BARK_ENABLED', 'false')
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.setenv('APP_RUNTIME', 'desktop')
    monkeypatch.setenv('AUTO_SYNC_ENABLED', 'true')  # Explicit scheduler tests; unattended polling remains disabled above.
