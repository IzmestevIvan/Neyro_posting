from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_CEILING


@dataclass(frozen=True)
class Plan:
    code: str
    price: int  # kopecks, never floats
    ai_daily_limit: int

    def public(self):
        return {'code': self.code, 'name': self.code.upper(), 'price_rub': self.price // 100,
                'ai_daily_limit': self.ai_daily_limit}


PLANS = {p.code: p for p in (
    Plan('plus', 39000, 5), Plan('pro', 49000, 10), Plan('expert', 59000, 15),
    Plan('creator', 99000, 25), Plan('unlimited', 149000, 50),
)}


def month_after(value: datetime) -> datetime:
    year, month = (value.year + 1, 1) if value.month == 12 else (value.year, value.month + 1)
    return value.replace(year=year, month=month, day=min(value.day, monthrange(year, month)[1]))


def prorated_upgrade(old: Plan, new: Plan, start: datetime, end: datetime, now: datetime) -> int:
    if not start <= now < end or new.price <= old.price:
        raise ValueError('Повышение тарифа недоступно')
    remaining = Decimal(str((end-now).total_seconds()))
    total = Decimal(str((end-start).total_seconds()))
    return max(100, int((Decimal(new.price-old.price)*remaining/total).quantize(Decimal(1), rounding=ROUND_CEILING)))


def money(kopecks: int) -> str:
    if type(kopecks) is not int or kopecks < 0:
        raise ValueError('invalid amount')
    return f'{kopecks // 100}.{kopecks % 100:02d}'
