from collections import defaultdict
from typing import Any, Dict


def new_store():
    return defaultdict(lambda: {
        'buyVolume': 0.0,
        'sellVolume': 0.0,
        'buyAmountMillion': 0.0,
        'sellAmountMillion': 0.0,
    })


def add_trade(store, code: str, quote: Dict[str, Any], to_number) -> None:
    raw_volume = quote.get('rawRealtime', {}).get('tradeVolume')
    signed_volume = to_number(raw_volume)
    price = float(quote.get('price') or 0)
    if price <= 0 or signed_volume == 0:
        return
    amount_million = abs(signed_volume) * price / 1_000_000
    if signed_volume > 0:
        store[code]['buyVolume'] += abs(signed_volume)
        store[code]['buyAmountMillion'] += amount_million
    else:
        store[code]['sellVolume'] += abs(signed_volume)
        store[code]['sellAmountMillion'] += amount_million


def metrics(store, code: str) -> Dict[str, Any]:
    row = store.get(code, {})
    buy_amount = float(row.get('buyAmountMillion') or 0)
    sell_amount = float(row.get('sellAmountMillion') or 0)
    total = buy_amount + sell_amount
    return {
        'buyVolume': int(row.get('buyVolume') or 0),
        'sellVolume': int(row.get('sellVolume') or 0),
        'buyAmountMillion': round(buy_amount, 3),
        'sellAmountMillion': round(sell_amount, 3),
        'netBuyAmountMillion': round(buy_amount - sell_amount, 3),
        'buyRatio': round((buy_amount / total) * 100, 2) if total > 0 else 0,
        'sellRatio': round((sell_amount / total) * 100, 2) if total > 0 else 0,
        'netBuyRatio': round(((buy_amount - sell_amount) / total) * 100, 2) if total > 0 else 0,
    }


def aggregate(stocks):
    buy_amount = sum(float(stock.get('buyAmountMillion') or 0) for stock in stocks)
    sell_amount = sum(float(stock.get('sellAmountMillion') or 0) for stock in stocks)
    total = buy_amount + sell_amount
    return {
        'buyAmountMillion': round(buy_amount, 3),
        'sellAmountMillion': round(sell_amount, 3),
        'netBuyAmountMillion': round(buy_amount - sell_amount, 3),
        'buyRatio': round((buy_amount / total) * 100, 2) if total > 0 else 0,
        'sellRatio': round((sell_amount / total) * 100, 2) if total > 0 else 0,
        'netBuyRatio': round(((buy_amount - sell_amount) / total) * 100, 2) if total > 0 else 0,
        'flow60sTradeAmountMillion': round(sum(float(stock.get('flow60sTradeAmountMillion') or 0) for stock in stocks), 3),
        'flow180sTradeAmountMillion': round(sum(float(stock.get('flow180sTradeAmountMillion') or 0) for stock in stocks), 3),
        'hotFlowCount': sum(1 for stock in stocks if stock.get('flowHot')),
    }
