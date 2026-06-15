# ============================================================
# FILE: TG/messages.py
# ROLE: Telegram/text message builders (formatting only)
# ============================================================

from __future__ import annotations

def build_funding_signal_message(
    template_str: str,
    symbol: str,
    kind: str,
    dom: str,
    slv: str,
    signal_type: str,
    comparison: str,
    price_spread: float,
    funding_spread: float,
    req_price_spread: str,
    req_funding_spread: str,
    fund_d: float,
    fund_s: float,
    ttf_d: float,
    ttf_s: float,
    interval_d: str,
    interval_s: str
) -> str:
    """Формирует ТГ сообщение из входных аргументов по шаблону."""
    clean_symbol = symbol.split('_')[0].upper()
    
    def format_ttf(ttf_sec: float) -> str:
        h = int(ttf_sec // 3600)
        m = int((ttf_sec % 3600) // 60)
        return f"{h}h {m}m"

    try:
        return template_str.format(
            symbol=clean_symbol,
            kind=kind,
            dom=dom.upper(),
            slv=slv.upper(),
            signal_type=signal_type,
            comparison=comparison,
            price_spread=price_spread,
            funding_spread=funding_spread,
            req_price_spread=req_price_spread,
            req_funding_spread=req_funding_spread,
            fund_d=fund_d,
            fund_s=fund_s,
            ttf_d_str=format_ttf(ttf_d),
            ttf_s_str=format_ttf(ttf_s),
            interval_d=f"{interval_d}h" if interval_d != "?" else "?",
            interval_s=f"{interval_s}h" if interval_s != "?" else "?"
        )
    except Exception as e:
        return f"Error formatting template: {e}\nRaw Data: {clean_symbol}"