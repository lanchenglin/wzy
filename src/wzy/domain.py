from dataclasses import dataclass, field
from decimal import Decimal
import re
from urllib.parse import urlsplit

OWN_URL = 'https://wzyp.cn/merchant/goods/list'
DEFAULT_SHOP = 'https://wzyp.cn/shop/85G05YIY'


def shop_url(value: str) -> str:
    match = re.fullmatch(r'https://wzyp\.cn/shop/([A-Za-z0-9_-]{1,64})', value)
    if not match:
        raise ValueError('仅允许 https://wzyp.cn/shop/店铺ID')
    return match[1]


def cdp_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}
                 and parsed.port is not None and 1 <= parsed.port <= 65535
                 and not parsed.username and not parsed.password
                 and parsed.path in {'', '/'} and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('CDP 必须为显式 loopback HTTP 地址和端口')
    return value.rstrip('/')


def money(value: str) -> Decimal:
    text = value.strip().removeprefix('¥').removeprefix('￥').strip()
    if not re.fullmatch(r'(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?', text):
        raise ValueError('无法识别展示价格')
    return Decimal(text.replace(',', '')).quantize(Decimal('0.01'))


@dataclass
class Product:
    key: str
    title: str
    category: str = ''
    description: str = ''
    price: str | None = None
    stock: str = ''
    status: str = ''
    url: str = ''
    consignment: bool = False
    matchable: bool = True


@dataclass
class Result:
    products: list[Product] = field(default_factory=list)
    status: str = 'unknown'
    declared_count: int | None = None
    errors: list[str] = field(default_factory=list)
    variant_coverage: str = 'unknown'
    pagination_terminal: str = 'not_observed'
