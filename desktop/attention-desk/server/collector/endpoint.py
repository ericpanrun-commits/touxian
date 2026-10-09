"""An explicitly configured mobile origin, independent of the desktop API."""
import ipaddress
from urllib.parse import urlsplit
from .network import lan_address
from .security import CollectorError


def public_base_url(value, *, allow_loopback_fixture=False):
    try:
        parsed = urlsplit(value)
        address = ipaddress.IPv4Address(parsed.hostname)
        if (parsed.scheme != 'http' or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
                or not parsed.port or not 1024 <= parsed.port <= 65535
                or not (lan_address(str(address)) or (allow_loopback_fixture and address.is_loopback))):
            raise ValueError()
        canonical = f'http://{address}:{parsed.port}'
        if canonical != value:
            raise ValueError()
        return canonical
    except (ValueError, TypeError):
        raise CollectorError('invalid_public_url', '手机入口必须使用当前物理局域网 IPv4 地址。', 409) from None
