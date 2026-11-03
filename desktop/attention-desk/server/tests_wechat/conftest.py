import pytest
from server.personal_store import PersonalStore


@pytest.fixture
def personal(tmp_path):
    store = PersonalStore(tmp_path / 'personal.sqlite3')
    store.initialize()
    return store


@pytest.fixture(autouse=True)
def never_touch_real_wechat_or_ocr(monkeypatch):
    from server.wechat_collect import windows
    from server.wechat_collect.media import MediaStore
    def forbidden(*args,**kwargs):
        pytest.fail('Offline WeChat tests must not inspect Windows or execute native OCR')
    monkeypatch.setattr(windows,'desktop_state',forbidden)
    monkeypatch.setattr(MediaStore,'_native_ocr',forbidden)
