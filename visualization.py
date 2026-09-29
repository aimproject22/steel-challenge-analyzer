"""Plotly figures for the Streamlit dashboard."""

from __future__ import annotations

from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from canonical import classify_event


STEEL_COLORS = ["#f97316", "#38bdf8", "#a3e635", "#facc15", "#c084fc"]
HOVER_COLUMNS = ["Run ID", "Run Date", "Steel User ID", "Sender", "Steel Grade"]


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame.get(column), errors="coerce")


def _hover(frame: pd.DataFrame) -> list[str]:
    return [column for column in HOVER_COLUMNS if column in frame.columns]


def line_by_run_date(frame: pd.DataFrame, metric: str, title: str):
    plot = frame.copy()
    plot["Run Date"] = pd.to_datetime(plot["Run Date"], errors="coerce")
    plot[metric] = _numeric(plot, metric)
    plot = plot.dropna(subset=["Run Date", metric]).sort_values("Run Date")
    return px.line(
        plot,
        x="Run Date",
        y=metric,
        markers=True,
        hover_data=_hover(plot),
        title=title,
        color_discrete_sequence=STEEL_COLORS,
    )


def histogram(frame: pd.DataFrame, metric: str, title: str):
    plot = frame.copy()
    plot[metric] = _numeric(plot, metric)
    return px.histogram(
        plot.dropna(subset=[metric]),
        x=metric,
        nbins=30,
        title=title,
        color_discrete_sequence=STEEL_COLORS,
    )


def scatter(frame: pd.DataFrame, x: str, y: str, title: str):
    plot = frame.copy()
    plot[x] = _numeric(plot, x)
    plot[y] = _numeric(plot, y)
    color = "Steel Grade" if "Steel Grade" in plot.columns else None
    return px.scatter(
        plot.dropna(subset=[x, y]),
        x=x,
        y=y,
        color=color,
        hover_data=_hover(plot),
        title=title,
        color_discrete_sequence=STEEL_COLORS,
    )


def grouped_average(frame: pd.DataFrame, group: str, metric: str, title: str):
    plot = frame.copy()
    plot[metric] = _numeric(plot, metric)
    grouped = (
        plot.dropna(subset=[group, metric])
        .groupby(group, as_index=False)[metric]
        .mean()
        .sort_values(metric)
    )
    return px.bar(
        grouped,
        x=group,
        y=metric,
        title=title,
        color_discrete_sequence=STEEL_COLORS,
    )


def grouped_count(frame: pd.DataFrame, group: str, title: str):
    grouped = frame.dropna(subset=[group]).groupby(group).size().reset_index(name="Run Count")
    return px.bar(
        grouped,
        x=group,
        y="Run Count",
        title=title,
        color_discrete_sequence=STEEL_COLORS,
    )


def feature_scatter(frame: pd.DataFrame, feature: str, target: str):
    return scatter(frame, feature, target, f"{feature} vs {target}")


def process_timeline(logs_df: pd.DataFrame, title: str = "공정 Timeline"):
    if logs_df.empty:
        return go.Figure().update_layout(title=title)
    plot = logs_df.copy()
    if "event_time" not in plot and "time" in plot:
        plot["event_time"] = plot["time"]
    if "event_seconds" not in plot:
        split = plot["event_time"].str.split(":", expand=True).apply(
            pd.to_numeric, errors="coerce"
        )
        plot["event_seconds"] = split[0] * 3600 + split[1] * 60 + split[2]
    plot["Minute"] = pd.to_numeric(plot["event_seconds"], errors="coerce") / 60
    if "category" not in plot:
        plot["category"] = plot["event"].map(classify_event)
    plot = plot.dropna(subset=["Minute"]).sort_values(["Minute", "log_no"])
    figure = px.scatter(
        plot,
        x="Minute",
        y="category",
        color="category",
        hover_data=[column for column in ("event_time", "event") if column in plot],
        title=title,
        color_discrete_sequence=STEEL_COLORS,
    )
    figure.update_traces(marker={"size": 12})
    figure.update_layout(showlegend=False, yaxis_title="Event Category")
    return figure

