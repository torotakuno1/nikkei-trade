#!/usr/bin/env python3
"""
EWMAC Multi-Instrument Trend-Following Backtest
=================================================
Runs EWMAC on N225M, Gold, USD/JPY with correct per-instrument specs.
Combines into a portfolio with IDM adjustment.

Usage:
    python ewmac_multi.py
"""

import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass, field


# ============================================================
# Per-Instrument Configuration
# ============================================================
# All specs are for the smallest tradeable contract via IBKR Japan

INSTRUMENTS = {
    'N225M': {
        'file': 'NK225M_1H_TV_FULL.csv',
        'label': '日経225ミニ',
        # ミニ先物: ×100倍。ただしマイクロ(×10)をシミュレート → point_value=10
        'point_value': 10,        # マイクロ先物 ×10
        'tick_size': 5,           # 5円刻み
        'commission': 50,         # 片道50円/枚
        'slippage_ticks': 1,      # 1ティック
        'currency': 'JPY',
        'fx_rate': 1.0,           # JPY口座なのでそのまま
    },
    'GOLD': {
        'file': 'GOLD_1H_TV_FULL.csv',
        'label': 'ゴールド(GC)',
        # CME Micro Gold (MGC): ×10 oz, price in USD/oz
        # 想定: MGCを日本口座で取引、USD建て
        'point_value': 10,        # MGC: $10/oz
        'tick_size': 0.10,        # $0.10刻み
        'commission': 200,        # ~$1.5 ≈ 200円/枚
        'slippage_ticks': 1,
        'currency': 'USD',
        'fx_rate': 150.0,         # USD/JPY概算（後で動的にすべきだが近似）
    },
    'USDJPY': {
        'file': 'USDJPY_1H_TV_FULL.csv',
        'label': 'USD/JPY',
        # CME Micro USD/JPY (M6J): ¥1,250,000 notional
        # IBKR FX: 最小25,000 USD単位
        # 簡易: 10,000 USD単位で計算
        'point_value': 10000,     # 10,000 USD × 1円 = ¥10,000/円
        'tick_size': 0.005,       # 0.5銭刻み
        'commission': 200,        # ~200円/取引
        'slippage_ticks': 1,
        'currency': 'JPY',        # PnL is directly in JPY
        'fx_rate': 1.0,
    },
}


@dataclass
class PortfolioConfig:
    capital: float = 2_000_000
    vol_target: float = 0.20
    max_contracts: int = 5
    ewmac_speeds: list = field(default_factory=lambda: [
        (8, 32), (16, 64), (32, 128), (64, 256)
    ])
    forecast_cap: float = 20.0
    forecast_target_abs: float = 10.0
    vol_lookback: int = 36
    fdm: float = 1.3
    position_inertia: float = 0.10
    ann_factor: float = 256


# ============================================================
# Core Functions (reused from ewmac_simple_tf.py)
# ============================================================
def load_tv_csv(filepath: str) -> pd.DataFrame:
    df = pd.read_csv(filepath)
    col_map = {}
    for col in df.columns:
        cl = col.strip().lower()
        if cl in ('time', 'date', 'datetime', 'timestamp'):
            col_map[col] = 'datetime'
        elif cl == 'open': col_map[col] = 'open'
        elif cl == 'high': col_map[col] = 'high'
        elif cl == 'low': col_map[col] = 'low'
        elif cl == 'close': col_map[col] = 'close'
        elif cl in ('volume', 'vol'): col_map[col] = 'volume'
    df = df.rename(columns=col_map)
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.set_index('datetime').sort_index()
    for col in ['open', 'high', 'low', 'close']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['open', 'high', 'low', 'close'])
    return df


def resample_to_daily(df):
    daily = df.groupby(df.index.date).agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
    })
    daily.index = pd.DatetimeIndex(daily.index)
    return daily


def ewma(series, span):
    return series.ewm(span=span, min_periods=span).mean()


def daily_price_vol(close, span=36):
    returns = close.diff()
    vol = returns.abs().ewm(span=span, min_periods=max(10, span // 2)).mean()
    # Floor at expanding 5th percentile
    vol_floor = vol.expanding().quantile(0.05)
    vol = vol.clip(lower=vol_floor)
    return vol


def calc_forecast_scalars(close, vol, speeds, target=10.0):
    scalars = {}
    for fast, slow in speeds:
        ema_f = ewma(close, fast)
        ema_s = ewma(close, slow)
        raw = (ema_f - ema_s) / vol
        med = raw.abs().expanding(min_periods=slow * 2).median().iloc[-1]
        scalars[(fast, slow)] = np.clip(target / med if med > 0 else 1.0, 1.0, 50.0)
    return scalars


def apply_inertia(ideal, buffer_pct=0.10):
    actual = pd.Series(0.0, index=ideal.index)
    current = 0.0
    for i in range(len(ideal)):
        target = ideal.iloc[i]
        if np.isnan(target):
            actual.iloc[i] = current
            continue
        rounded = round(target)
        change = abs(rounded - current)
        threshold = max(abs(current) * buffer_pct, 0.5)
        if change >= threshold:
            current = rounded
        actual.iloc[i] = current
    return actual


# ============================================================
# Single Instrument Backtest
# ============================================================
def backtest_instrument(name: str, spec: dict, cfg: PortfolioConfig,
                        n_instruments: int = 1) -> dict:
    """Run EWMAC on a single instrument with proper specs."""
    filepath = Path(spec['file'])
    if not filepath.exists():
        print(f"  ⚠ {name}: ファイルなし ({filepath})")
        return None

    df = load_tv_csv(str(filepath))
    daily = resample_to_daily(df)
    close = daily['close'].copy()

    print(f"\n{'='*55}")
    print(f"  {name} ({spec['label']})")
    print(f"  期間: {daily.index[0].date()} - {daily.index[-1].date()} ({len(daily)}日足)")
    print(f"  point_value={spec['point_value']}, tick={spec['tick_size']}")
    print(f"{'='*55}")

    # Volatility
    vol = daily_price_vol(close, cfg.vol_lookback)

    # Forecast scalars
    scalars = calc_forecast_scalars(close, vol, cfg.ewmac_speeds)

    # Individual forecasts
    forecasts = {}
    for fast, slow in cfg.ewmac_speeds:
        ema_f = ewma(close, fast)
        ema_s = ewma(close, slow)
        raw = (ema_f - ema_s) / vol * scalars[(fast, slow)]
        forecasts[(fast, slow)] = raw.clip(-cfg.forecast_cap, cfg.forecast_cap)

    # Combine (equal weight)
    fc_df = pd.DataFrame(forecasts)
    combined = fc_df.mean(axis=1) * cfg.fdm
    combined = combined.clip(-cfg.forecast_cap, cfg.forecast_cap)

    # IDM lookup
    idm_table = {1: 1.0, 2: 1.2, 3: 1.48, 4: 1.56, 5: 1.7}
    idm = idm_table.get(n_instruments, min(2.5, 1.0 + 0.5 * np.log(n_instruments)))

    # Position sizing
    w_i = 1.0 / n_instruments
    annual_vol = vol * np.sqrt(cfg.ann_factor)
    # Convert to JPY terms if needed
    fx = spec['fx_rate']
    numerator = cfg.capital * cfg.vol_target * idm * w_i * combined * cfg.fdm
    denominator = cfg.forecast_target_abs * annual_vol * spec['point_value'] * fx

    ideal_pos = numerator / denominator
    ideal_pos = ideal_pos.clip(-cfg.max_contracts, cfg.max_contracts)
    actual_pos = apply_inertia(ideal_pos, cfg.position_inertia)

    # PnL calculation
    price_change = close.diff()
    pos_shifted = actual_pos.shift(1).fillna(0)
    gross_pnl = pos_shifted * price_change * spec['point_value'] * fx

    # Costs
    pos_change = actual_pos.diff().fillna(0).abs()
    slippage_per = spec['slippage_ticks'] * spec['tick_size'] * spec['point_value'] * fx
    costs = pos_change * (spec['commission'] + slippage_per)
    net_pnl = gross_pnl - costs

    # Equity
    equity = cfg.capital + net_pnl.cumsum()

    # Stats
    peak = equity.expanding().max()
    dd = equity - peak
    max_dd = abs(dd.min())
    total_pnl = net_pnl.sum()

    daily_ret = net_pnl / cfg.capital
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(cfg.ann_factor) if daily_ret.std() > 0 else 0
    rf = total_pnl / max_dd if max_dd > 0 else 0

    # Yearly
    yearly = {}
    for year in sorted(net_pnl.index.year.unique()):
        yearly[year] = net_pnl[net_pnl.index.year == year].sum()

    # Trade count (direction changes)
    sign_changes = (np.sign(actual_pos) != np.sign(actual_pos.shift(1))).sum()

    # Print
    print(f"\n  Forecast Scalars: {', '.join(f'({f},{s}):{sc:.1f}' for (f,s), sc in scalars.items())}")
    print(f"\n  NP: {total_pnl/10000:+.1f}万  DD: {max_dd/10000:.1f}万  RF: {rf:.2f}  Sharpe: {sharpe:.3f}")
    print(f"  平均枚数: {actual_pos.abs().mean():.2f}  最大: {actual_pos.abs().max():.0f}")
    print(f"  コスト: {costs.sum()/10000:.1f}万  ({costs.sum()/gross_pnl.sum()*100:.1f}%)" if gross_pnl.sum() > 0 else f"  コスト: {costs.sum()/10000:.1f}万")

    print(f"\n  年別:")
    for year, pnl in sorted(yearly.items()):
        print(f"    {year}: {pnl/10000:+8.1f}万")

    return {
        'name': name,
        'daily': daily,
        'net_pnl': net_pnl,
        'equity': equity,
        'position': actual_pos,
        'forecast': combined,
        'max_dd': max_dd,
        'total_pnl': total_pnl,
        'sharpe': sharpe,
        'rf': rf,
        'yearly': yearly,
        'costs': costs,
    }


# ============================================================
# Portfolio Combination
# ============================================================
def combine_portfolio(results: dict, cfg: PortfolioConfig):
    """Combine individual instrument PnLs into portfolio."""
    print(f"\n{'='*55}")
    print(f"  ポートフォリオ合算")
    print(f"{'='*55}")

    # Align dates
    all_pnl = {}
    for name, r in results.items():
        if r is not None:
            all_pnl[name] = r['net_pnl']

    pnl_df = pd.DataFrame(all_pnl)
    # Use intersection of dates
    pnl_df = pnl_df.dropna(how='all')
    pnl_df = pnl_df.fillna(0)  # Missing dates = no PnL

    combined_pnl = pnl_df.sum(axis=1)
    equity = cfg.capital + combined_pnl.cumsum()
    peak = equity.expanding().max()
    dd = equity - peak
    max_dd = abs(dd.min())
    total_pnl = combined_pnl.sum()

    daily_ret = combined_pnl / cfg.capital
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(cfg.ann_factor) if daily_ret.std() > 0 else 0
    rf = total_pnl / max_dd if max_dd > 0 else 0

    # Correlations
    print(f"\n  月次PnL相関:")
    monthly = pnl_df.resample('ME').sum()
    if len(monthly) > 6:
        corr = monthly.corr()
        for i, n1 in enumerate(corr.columns):
            for j, n2 in enumerate(corr.columns):
                if j > i:
                    print(f"    {n1} vs {n2}: {corr.iloc[i,j]:.3f}")

    # Individual DD sum vs combined DD
    individual_dd_sum = sum(r['max_dd'] for r in results.values() if r is not None)
    diversification_benefit = 1 - max_dd / individual_dd_sum if individual_dd_sum > 0 else 0

    print(f"\n  合算NP:      {total_pnl/10000:+.1f}万")
    print(f"  合算DD:      {max_dd/10000:.1f}万")
    print(f"  合算RF:      {rf:.2f}")
    print(f"  合算Sharpe:  {sharpe:.3f}")
    print(f"  DD/口座:     {max_dd/cfg.capital*100:.1f}%")
    print(f"\n  個別DD合計:  {individual_dd_sum/10000:.1f}万")
    print(f"  分散効果:    {diversification_benefit*100:.0f}%軽減")

    # Yearly
    yearly = {}
    for year in sorted(combined_pnl.index.year.unique()):
        mask = combined_pnl.index.year == year
        y_pnl = combined_pnl[mask].sum()
        yearly[year] = y_pnl
        parts = {name: pnl_df[name][mask].sum() for name in pnl_df.columns}
        parts_str = '  '.join(f"{n}:{v/10000:+.1f}" for n, v in parts.items())
        print(f"    {year}: {y_pnl/10000:+8.1f}万  ({parts_str})")

    return {
        'equity': equity,
        'pnl': combined_pnl,
        'max_dd': max_dd,
        'total_pnl': total_pnl,
        'sharpe': sharpe,
        'rf': rf,
        'yearly': yearly,
    }


# ============================================================
# Comparison Table
# ============================================================
def print_comparison(results: dict, portfolio: dict, cfg: PortfolioConfig):
    """Print comparison table including v6/案C benchmarks."""
    print(f"\n{'='*75}")
    print(f"  最終比較表")
    print(f"{'='*75}")

    headers = ['指標', 'v6 VT20', '案C VT1+DD', 'v6+案C', 'EWMAC N225',
               'EWMAC Gold', 'EWMAC USDJPY', 'EWMAC合算']

    # Build rows
    rows = []

    def val(r, key, fmt='.1f', div=10000):
        if r is None: return '—'
        v = r.get(key, r.get('total_pnl', 0))
        if div: v = v / div
        return f'{v:{fmt}}'

    n225 = results.get('N225M')
    gold = results.get('GOLD')
    usdjpy = results.get('USDJPY')

    print(f"\n  {'':>14s} | {'v6 VT20':>10s} | {'案C+DD':>10s} | {'v6+案C':>10s} | {'N225M':>10s} | {'Gold':>10s} | {'USDJPY':>10s} | {'EWMAC合算':>10s}")
    print(f"  {'-'*14}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}")

    def r(label, v6, casec, both, *ewmac_vals):
        vals = [f'{v:>10s}' for v in [v6, casec, both] + list(ewmac_vals)]
        print(f"  {label:>14s} | {' | '.join(vals)}")

    n225_np = f"{n225['total_pnl']/10000:+.1f}" if n225 else '—'
    gold_np = f"{gold['total_pnl']/10000:+.1f}" if gold else '—'
    usdjpy_np = f"{usdjpy['total_pnl']/10000:+.1f}" if usdjpy else '—'
    port_np = f"{portfolio['total_pnl']/10000:+.1f}" if portfolio else '—'

    n225_dd = f"{n225['max_dd']/10000:.1f}" if n225 else '—'
    gold_dd = f"{gold['max_dd']/10000:.1f}" if gold else '—'
    usdjpy_dd = f"{usdjpy['max_dd']/10000:.1f}" if usdjpy else '—'
    port_dd = f"{portfolio['max_dd']/10000:.1f}" if portfolio else '—'

    n225_rf = f"{n225['rf']:.2f}" if n225 else '—'
    gold_rf = f"{gold['rf']:.2f}" if gold else '—'
    usdjpy_rf = f"{usdjpy['rf']:.2f}" if usdjpy else '—'
    port_rf = f"{portfolio['rf']:.2f}" if portfolio else '—'

    n225_sr = f"{n225['sharpe']:.3f}" if n225 else '—'
    gold_sr = f"{gold['sharpe']:.3f}" if gold else '—'
    usdjpy_sr = f"{usdjpy['sharpe']:.3f}" if usdjpy else '—'
    port_sr = f"{portfolio['sharpe']:.3f}" if portfolio else '—'

    r('NP(万)', '894.2', '509.0', '1403.2', n225_np, gold_np, usdjpy_np, port_np)
    r('DD(万)', '72.6', '57.7', '85.9', n225_dd, gold_dd, usdjpy_dd, port_dd)
    r('RF', '12.23', '8.77', '16.33', n225_rf, gold_rf, usdjpy_rf, port_rf)
    r('Sharpe', '0.309', '0.345', '—', n225_sr, gold_sr, usdjpy_sr, port_sr)
    r('期間', '6年', '6年', '6年', '4年', '4年', '3年', '共通期間')

    print(f"\n  ※ v6/案Cは2020/1-2026/3の6年。EWMACはデータ制約で4年/3年。直接比較は参考値。")
    print(f"  ※ EWMAC N225MはEWMAC(64,256)のウォームアップに256日消費→実質テスト期間は短い。")


# ============================================================
# Main
# ============================================================
def main():
    cfg = PortfolioConfig()

    import os
    os.chdir('/home/claude')

    # Run each instrument
    results = {}
    for name, spec in INSTRUMENTS.items():
        results[name] = backtest_instrument(name, spec, cfg, n_instruments=3)

    # Combine portfolio
    valid = {k: v for k, v in results.items() if v is not None}
    portfolio = combine_portfolio(valid, cfg) if len(valid) > 1 else None

    # Comparison
    print_comparison(results, portfolio, cfg)


if __name__ == '__main__':
    main()
