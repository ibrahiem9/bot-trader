"""Thin adapter with no live URL or live-account execution option."""
from __future__ import annotations

from .data import credentials


def _value(value):
    return getattr(value, "value", value)


class AlpacaBroker:
    def __init__(self):
        from alpaca.trading.client import TradingClient

        self.client = TradingClient(*credentials(), paper=True)
        # alpaca-py's default requests session has no timeout. Bound every call,
        # and let our persisted-intention reconciliation handle ambiguous errors.
        from functools import partial

        self.client._session.request = partial(self.client._session.request, timeout=(3, 10))
        self.client._retry = 0

    def account(self):
        a = self.client.get_account()
        return {"id": str(a.id), "equity": float(a.equity), "cash": float(a.cash),
                "blocked": bool(a.trading_blocked or a.account_blocked),
                "short_market_value": float(a.short_market_value)}

    def positions(self):
        return {p.symbol: {"qty": float(p.qty), "market_value": float(p.market_value)}
                for p in self.client.get_all_positions()}

    @staticmethod
    def _order(o):
        return {"client_id": o.client_order_id, "status": _value(o.status),
                "symbol": o.symbol, "side": _value(o.side),
                "filled_qty": float(o.filled_qty or 0), "id": str(o.id)}

    def lookup(self, client_id):
        from alpaca.common.exceptions import APIError

        try:
            return self._order(self.client.get_order_by_client_id(client_id))
        except APIError as exc:
            if exc.status_code == 404:
                return None
            raise

    def open_orders(self):
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        return [self._order(o) for o in self.client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)
        )]

    def eligible(self, symbol):
        a = self.client.get_asset(symbol)
        return bool(a.tradable and a.fractionable and _value(a.status) == "active")

    def submit(self, client_id, symbol, side, notional=None, qty=None):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        request = MarketOrderRequest(symbol=symbol, side=OrderSide(side),
                                     time_in_force=TimeInForce.DAY,
                                     client_order_id=client_id, notional=notional, qty=qty)
        return self._order(self.client.submit_order(order_data=request))

    def cancel(self, order):
        self.client.cancel_order_by_id(order["id"])

    def market_open(self):
        return bool(self.client.get_clock().is_open)
