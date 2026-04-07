from __future__ import annotations
"""
ICT 차트 시각화 - ICT 분석 결과를 캔들스틱 차트 위에 오버레이하여 PNG로 저장합니다.

이 파일은 나중에 사용자가 직접 제공하는 파일로 교체될 수 있습니다.
교체 시 generate_ict_chart(df, ict_result, symbol, output_path) 인터페이스만 유지하면 됩니다.
"""
import matplotlib
matplotlib.use("Agg")  # GUI 없이 렌더링

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.dates as mdates
import mplfinance as mpf
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

from src.config_loader import get_config
from src.file_manager import get_session_id
from src.logger import setup_logger

logger = setup_logger("ict_chart")

# 최근 N일만 차트로 표시 (전체 5년은 너무 넓음)
CHART_DISPLAY_DAYS = 120


def generate_ict_chart(
    df: pd.DataFrame,
    ict_result: dict,
    symbol: str,
    output_path: str = None,
) -> Path:
    """
    ICT 분석 결과가 오버레이된 캔들스틱 차트를 생성합니다.

    Args:
        df: OHLCV DataFrame
        ict_result: run_ict_analysis() 결과
        symbol: 종목명
        output_path: 저장 경로 (None이면 자동 생성)

    Returns:
        저장된 파일 경로
    """
    if output_path is None:
        cfg = get_config()
        chart_dir = Path(cfg["paths"]["charts"])
        chart_dir.mkdir(parents=True, exist_ok=True)
        session_id = get_session_id()
        output_path = chart_dir / f"{session_id}_{symbol}.png"
    else:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    # 최근 N일만 표시
    plot_df = df.tail(CHART_DISPLAY_DAYS).copy()
    start_idx = len(df) - CHART_DISPLAY_DAYS

    # mplfinance용 인덱스 설정
    plot_df = plot_df.set_index("timestamp")
    plot_df.index = pd.DatetimeIndex(plot_df.index)

    # 커스텀 스타일
    mc = mpf.make_marketcolors(
        up="#26a69a", down="#ef5350",
        edge="inherit",
        wick="inherit",
        volume="in",
    )
    style = mpf.make_mpf_style(
        marketcolors=mc,
        gridstyle=":",
        gridcolor="#2a2a2a",
        facecolor="#1a1a2e",
        figcolor="#1a1a2e",
        rc={"axes.labelcolor": "white", "xtick.color": "white", "ytick.color": "white"},
    )

    # 오버레이 데이터 수집
    addplots = []
    fill_between_args = []

    # ─── Order Blocks 표시 ────────────────────────────
    ob_data = _prepare_order_block_data(ict_result.get("order_blocks", []), plot_df, start_idx)

    # ─── FVG 표시 ─────────────────────────────────────
    fvg_data = _prepare_fvg_data(ict_result.get("fvg", []), plot_df, start_idx)

    # ─── BOS/CHoCH 마커 ──────────────────────────────
    bos_choch_data = _prepare_structure_markers(
        ict_result.get("market_structure", []), plot_df, start_idx
    )

    # ─── Premium/Discount 존 ─────────────────────────
    pd_info = ict_result.get("premium_discount", {})

    # ─── 차트 생성 ────────────────────────────────────
    fig, axes = mpf.plot(
        plot_df,
        type="candle",
        style=style,
        volume=True,
        figsize=(20, 12),
        returnfig=True,
        panel_ratios=(4, 1),
        tight_layout=True,
    )

    ax_price = axes[0]
    ax_vol = axes[2]

    # Order Blocks 그리기
    for ob in ob_data:
        color = "#26a69a40" if ob["type"] == "bullish" else "#ef535040"
        edge_color = "#26a69a" if ob["type"] == "bullish" else "#ef5350"
        rect = mpatches.FancyBboxPatch(
            (ob["x_start"], ob["bottom"]),
            ob["width"],
            ob["top"] - ob["bottom"],
            boxstyle="round,pad=0",
            facecolor=color,
            edgecolor=edge_color,
            linewidth=0.8,
        )
        ax_price.add_patch(rect)

    # FVG 그리기
    for fvg in fvg_data:
        color = "#2196f340" if fvg["type"] == "bullish" else "#ff980040"
        ax_price.axhspan(
            fvg["bottom"], fvg["top"],
            xmin=fvg["x_norm_start"], xmax=fvg["x_norm_end"],
            facecolor=color, alpha=0.3,
        )

    # BOS/CHoCH 마커 그리기
    for marker in bos_choch_data:
        color = "#00e676" if marker["direction"] == "bullish" else "#ff1744"
        style_char = "^" if marker["type"] == "BOS" else "D"
        ax_price.plot(
            marker["x"], marker["price"],
            marker=style_char, color=color,
            markersize=8, zorder=5,
        )
        ax_price.annotate(
            marker["type"],
            (marker["x"], marker["price"]),
            textcoords="offset points",
            xytext=(0, 12 if marker["direction"] == "bullish" else -12),
            fontsize=7, color=color, ha="center", fontweight="bold",
        )

    # Liquidity 레벨 그리기
    for liq in ict_result.get("liquidity", []):
        if liq.get("swept"):
            continue
        color = "#ffeb3b" if liq["type"] == "buy_side" else "#e040fb"
        linestyle = "--"
        ax_price.axhline(
            y=liq["price"], color=color, linestyle=linestyle,
            linewidth=0.7, alpha=0.6,
        )

    # Premium/Discount 존 표시
    if pd_info:
        eq_line = (pd_info.get("swing_high", 0) + pd_info.get("swing_low", 0)) / 2
        if eq_line > 0:
            ax_price.axhline(y=eq_line, color="#ffffff", linestyle="-.", linewidth=0.5, alpha=0.4)
            ax_price.text(
                len(plot_df) - 1, eq_line, " EQ",
                color="#ffffff", fontsize=7, alpha=0.6, va="center",
            )

        # OTE 존
        ote_top = pd_info.get("ote_top", 0)
        ote_bottom = pd_info.get("ote_bottom", 0)
        if ote_top > 0 and ote_bottom > 0:
            ax_price.axhspan(ote_bottom, ote_top, facecolor="#9c27b020", alpha=0.2)
            ax_price.text(
                len(plot_df) - 1, (ote_top + ote_bottom) / 2, " OTE",
                color="#9c27b0", fontsize=7, alpha=0.8, va="center",
            )

    # 제목 및 요약 정보
    summary = ict_result.get("summary", {})
    trend = summary.get("current_trend", "N/A")
    zone = summary.get("premium_discount_zone", "N/A")
    fib = summary.get("fib_level", 0)

    title = f"{symbol} | ICT Analysis | Trend: {trend.upper()} | Zone: {zone} (Fib: {fib:.3f})"
    ax_price.set_title(title, color="white", fontsize=14, fontweight="bold", pad=15)

    # 범례
    legend_elements = [
        mpatches.Patch(facecolor="#26a69a40", edgecolor="#26a69a", label="Bullish OB"),
        mpatches.Patch(facecolor="#ef535040", edgecolor="#ef5350", label="Bearish OB"),
        mpatches.Patch(facecolor="#2196f340", label="Bullish FVG"),
        mpatches.Patch(facecolor="#ff980040", label="Bearish FVG"),
        plt.Line2D([0], [0], color="#ffeb3b", linestyle="--", label="Buy-side Liq"),
        plt.Line2D([0], [0], color="#e040fb", linestyle="--", label="Sell-side Liq"),
        mpatches.Patch(facecolor="#9c27b020", label="OTE Zone"),
    ]
    ax_price.legend(
        handles=legend_elements, loc="upper left",
        fontsize=7, facecolor="#1a1a2e", edgecolor="#444",
        labelcolor="white",
    )

    fig.savefig(str(output_path), dpi=150, bbox_inches="tight", facecolor="#1a1a2e")
    plt.close(fig)

    logger.info(f"[{symbol}] ICT chart saved to {output_path}")
    return output_path


# ─── 헬퍼 함수들 ─────────────────────────────────────────

def _prepare_order_block_data(order_blocks: list, plot_df: pd.DataFrame, start_idx: int) -> list:
    """Order Block을 차트 좌표로 변환합니다."""
    result = []
    n = len(plot_df)

    for ob in order_blocks:
        rel_idx = ob["index"] - start_idx
        if rel_idx < 0 or rel_idx >= n:
            continue
        if ob.get("mitigated"):
            continue

        # OB 영역을 현재까지 확장
        result.append({
            "type": ob["type"],
            "x_start": rel_idx,
            "width": n - rel_idx,
            "top": ob["top"],
            "bottom": ob["bottom"],
        })

    return result


def _prepare_fvg_data(fvgs: list, plot_df: pd.DataFrame, start_idx: int) -> list:
    """FVG를 차트 좌표로 변환합니다."""
    result = []
    n = len(plot_df)

    for fvg in fvgs:
        rel_idx = fvg["index"] - start_idx
        if rel_idx < 0 or rel_idx >= n:
            continue
        if fvg.get("filled"):
            continue

        result.append({
            "type": fvg["type"],
            "top": fvg["top"],
            "bottom": fvg["bottom"],
            "x_norm_start": max(0, rel_idx / n),
            "x_norm_end": 1.0,
        })

    return result


def _prepare_structure_markers(structures: list, plot_df: pd.DataFrame, start_idx: int) -> list:
    """BOS/CHoCH 마커를 차트 좌표로 변환합니다."""
    result = []
    n = len(plot_df)

    for s in structures:
        rel_idx = s["index"] - start_idx
        if rel_idx < 0 or rel_idx >= n:
            continue

        result.append({
            "type": s["type"],
            "direction": s["direction"],
            "x": rel_idx,
            "price": s["price"],
        })

    return result
