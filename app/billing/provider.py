"""YooKassa boundary: secrets stay in headers; errors do not expose provider bodies."""
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import httpx


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    shop_id: str
    secret: str
    environment: str
    enabled: bool
    origin: str
    vat_code: int | None
    payment_subject: str
    payment_mode: str
    fiscal_ready: bool
    receipt_mode: str = 'yookassa_54fz'

    @classmethod
    def read(cls):
        vat = os.getenv('YOOKASSA_VAT_CODE', '')
        return cls(os.getenv('YOOKASSA_SHOP_ID', ''), os.getenv('YOOKASSA_SECRET_KEY', ''),
                   os.getenv('YOOKASSA_ENV', 'test'), os.getenv('BILLING_ENABLED') == '1',
                   os.getenv('PUBLIC_URL', '').rstrip('/'), int(vat) if vat.isdigit() else None,
                   os.getenv('YOOKASSA_PAYMENT_SUBJECT', ''), os.getenv('YOOKASSA_PAYMENT_MODE', ''),
                   os.getenv('YOOKASSA_FISCAL_READY') == '1',
                   os.getenv('YOOKASSA_RECEIPT_MODE','yookassa_54fz'))

    def ready(self):
        url = urlsplit(self.origin)
        fiscal = self.receipt_mode == 'npd_manual' or (
            self.receipt_mode == 'yookassa_54fz' and self.vat_code in range(1,13)
            and self.payment_subject in ('service','intellectual_activity')
            and self.payment_mode == 'full_payment')
        return (self.enabled and self.shop_id.isdigit() and self.environment in ('test','live')
                and self.secret.startswith(self.environment+'_')
                and url.scheme == 'https' and bool(url.hostname) and not url.username
                and not url.query and not url.fragment and not url.path
                and self.fiscal_ready and fiscal)


class YooKassa:
    def __init__(self, settings=None, transport=None):
        self.settings = settings or Settings.read()
        self.transport = transport

    async def request(self, method, path, *, body=None, key=None):
        if not self.settings.ready():
            raise ProviderError('Оплата пока не настроена')
        headers = {'Idempotence-Key': key} if key else {}
        try:
            # Retry only connection establishment, before any HTTP request bytes
            # are sent. Unknown outcomes after sending stay in durable order logic.
            transport = self.transport or httpx.AsyncHTTPTransport(retries=2, trust_env=False)
            async with httpx.AsyncClient(base_url='https://api.yookassa.ru/v3/',
                    auth=(self.settings.shop_id,self.settings.secret), timeout=httpx.Timeout(15, connect=5),
                    transport=transport, follow_redirects=False, trust_env=False) as client:
                response = await client.request(method,path,json=body,headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError('Статус платежа уточняется. Повторное списание не создаётся.') from None
        if response.status_code not in (200,201):
            raise ProviderError('Платёжный сервис временно недоступен. Статус уточняется.')
        try:
            value = response.json()
        except ValueError:
            raise ProviderError('Некорректный ответ платёжного сервиса') from None
        if not isinstance(value,dict):
            raise ProviderError('Некорректный ответ платёжного сервиса')
        return value


def amount_kopecks(value):
    if not isinstance(value,dict) or value.get('currency') != 'RUB':
        raise ValueError('wrong currency')
    try:
        amount = Decimal(value['value'])*100
        if not amount.is_finite() or amount != amount.to_integral_value() or amount < 0:
            raise ValueError('invalid amount')
        return int(amount)
    except (InvalidOperation,KeyError,TypeError):
        raise ValueError('invalid amount') from None


def safe_confirmation(value):
    if not value:
        return None
    url = urlsplit(value)
    if (url.scheme != 'https' or url.username or url.password or url.port not in (None,443)
            or url.hostname not in ('yoomoney.ru','yookassa.ru')):
        raise ValueError('untrusted confirmation URL')
    return value
