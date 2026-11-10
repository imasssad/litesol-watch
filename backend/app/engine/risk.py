from datetime import datetime, UTC
from app.config import settings
from app.models.schemas import Opportunity, Quote


class RiskEngine:
    def __init__(self):
        self.kill_switch = False
        self.daily_pnl_usd = 0.0
        self.failed_trade_count = 0
        self.last_reset_date = datetime.now(UTC).date()

    def reset_daily_if_needed(self) -> None:
        today = datetime.now(UTC).date()
        if today != self.last_reset_date:
            self.daily_pnl_usd = 0.0
            self.failed_trade_count = 0
            self.last_reset_date = today

    def enable_kill_switch(self):
        self.kill_switch = True

    def disable_kill_switch(self):
        self.kill_switch = False
        self.failed_trade_count = 0

    def record_failure(self) -> None:
        self.failed_trade_count += 1
        if self.failed_trade_count >= settings.max_consecutive_failures:
            self.enable_kill_switch()

    def record_pnl(self, pnl_usd: float) -> None:
        self.reset_daily_if_needed()
        self.daily_pnl_usd += pnl_usd
        if self.daily_pnl_usd <= -abs(settings.max_daily_loss_usd):
            self.enable_kill_switch()

    def validate_quote_freshness(self, quotes: list[Quote]) -> tuple[bool, str]:
        now = datetime.now(UTC)
        for q in quotes:
            age = (now - q.ts).total_seconds()
            if age > settings.max_quote_age_seconds:
                return False, f"stale quote from {q.venue}: {age:.2f}s old"
        return True, "quotes fresh"

    def validate(self, opportunity: Opportunity, quotes: list[Quote] | None = None) -> tuple[bool, str]:
        self.reset_daily_if_needed()
        if self.kill_switch:
            return False, "kill switch enabled"
        if opportunity.trade_size_usd > settings.max_trade_usd:
            return False, "trade size exceeds max_trade_usd"
        if self.daily_pnl_usd <= -abs(settings.max_daily_loss_usd):
            return False, "max daily loss reached"
        if opportunity.net_spread_bps < settings.min_net_spread_bps:
            return False, "net spread below threshold"
        if quotes:
            ok, reason = self.validate_quote_freshness(quotes)
            if not ok:
                return False, reason
        return True, "risk checks passed"
