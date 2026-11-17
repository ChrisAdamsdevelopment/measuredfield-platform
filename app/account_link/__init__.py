"""Owner-only account-linked Windows computer client; never expose credentials to MCP."""
from .controller import AccountLinkController, AccountBoundPairingController
from .client import DeviceApi, DeviceStatus, DeviceClientError, DeviceRevoked
from .secret_store import WindowsCredentialStore, SecureStoreUnavailable

__all__ = ["AccountLinkController", "AccountBoundPairingController", "DeviceApi", "DeviceStatus", "DeviceClientError", "DeviceRevoked", "WindowsCredentialStore", "SecureStoreUnavailable"]
