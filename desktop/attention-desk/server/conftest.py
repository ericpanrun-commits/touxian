"""Network polling is exercised explicitly, never as a pytest startup side effect."""
import pytest


@pytest.fixture(autouse=True)
def no_unattended_source_polling(monkeypatch):
    monkeypatch.setenv('ATTENTION_OFFICIAL_POLL', '0')
    monkeypatch.setenv('ATTENTION_REMINDER_POLL', '0')
