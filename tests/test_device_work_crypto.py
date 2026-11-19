"""Per-tenant encryption separation and key rotation without customer data."""
import uuid

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.services.device_work import _box, KEY_NAME, WorkQueueDenied


def test_tenant_ciphertexts_cannot_be_decrypted_with_other_tenant_key(monkeypatch):
    monkeypatch.setenv(KEY_NAME, Fernet.generate_key().decode("ascii"))
    monkeypatch.delenv("MF_DEVICE_WORK_FERNET_PREVIOUS_KEYS", raising=False)
    a, b = uuid.uuid4(), uuid.uuid4()
    blob = _box(a).encrypt(b"owner approved file edit")
    assert _box(a).decrypt(blob) == b"owner approved file edit"
    with pytest.raises(InvalidToken):
        _box(b).decrypt(blob)


def test_previous_master_key_can_be_read_during_safe_rotation(monkeypatch):
    tenant = uuid.uuid4()
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setenv(KEY_NAME, old)
    monkeypatch.delenv("MF_DEVICE_WORK_FERNET_PREVIOUS_KEYS", raising=False)
    original = _box(tenant).encrypt(b"old queued work")
    monkeypatch.setenv(KEY_NAME, new)
    monkeypatch.setenv("MF_DEVICE_WORK_FERNET_PREVIOUS_KEYS", old)
    assert _box(tenant).decrypt(original) == b"old queued work"
    assert _box(tenant).decrypt(_box(tenant).encrypt(b"new queued work")) == b"new queued work"
    monkeypatch.delenv("MF_DEVICE_WORK_FERNET_PREVIOUS_KEYS")
    with pytest.raises(InvalidToken):
        _box(tenant).decrypt(original)


def test_bad_master_or_too_many_previous_keys_fail_closed(monkeypatch):
    tenant = uuid.uuid4()
    monkeypatch.setenv(KEY_NAME, "not-a-valid-key")
    with pytest.raises(WorkQueueDenied):
        _box(tenant)
    monkeypatch.setenv(KEY_NAME, Fernet.generate_key().decode())
    monkeypatch.setenv(
        "MF_DEVICE_WORK_FERNET_PREVIOUS_KEYS",
        ";".join(Fernet.generate_key().decode() for _ in range(4)),
    )
    with pytest.raises(WorkQueueDenied, match="Too many"):
        _box(tenant)
