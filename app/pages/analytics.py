"""
Analytics page — pipeline health, queue throughput, case lifecycle, agent activity.
"""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from data import (
    ui_agent_daily,
    ui_bronze_latency,
    ui_case_transitions,
    ui_daily_triage,
    ui_pipeline_counts,
    ui_pipeline_health,
)

st.title("\U0001f4ca Analytics")

if st.button("\U0001f504 Refresh", key="btn_refresh_analytics"):
    st.cache_data.clear()
    st.rerun()

# ------------------------------------------------------------------ #
# a. Pipeline — volume and velocity                                   #
# ------------------------------------------------------------------ #
st.header("Pipeline — volume and velocity")

# Volume metrics
try:
    df_counts = ui_pipeline_counts()
    if df_counts.empty:
        st.info("Volume counts unavailable — the SQL warehouse may be starting.")
    else:
        row = df_counts.iloc[0]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Bronze events",  f"{int(row['bronze']):,}")
        c2.metric("Silver edits",   f"{int(row['edits']):,}")
        c3.metric("Candidates",     f"{int(row['candidates']):,}")
        c4.metric("In queue",       f"{int(row['queue']):,}")
except Exception as _exc:
    st.error(f"Could not load volume counts: {_exc}")

# Ingest latency chart
try:
    df_lat = ui_bronze_latency()
    if df_lat.empty:
        st.info("No latency data in the last hour — is the connector job running?")
    else:
        # Metrics for the most recent minute
        latest = df_lat.iloc[-1]
        lc1, lc2 = st.columns(2)
        lc1.metric("Latest p50 latency", f"{float(latest['p50_seconds']):.1f} s")
        lc2.metric("Latest p95 latency", f"{float(latest['p95_seconds']):.1f} s")

        # Reshape for Altair
        df_melt = df_lat.melt(
            id_vars="minute",
            value_vars=["p50_seconds", "p95_seconds"],
            var_name="metric",
            value_name="seconds",
        )
        df_melt["metric"] = df_melt["metric"].map(
            {"p50_seconds": "p50", "p95_seconds": "p95"}
        )

        lines = (
            alt.Chart(df_melt)
            .mark_line()
            .encode(
                x=alt.X("minute:T", title="Time (UTC)"),
                y=alt.Y("seconds:Q", title="Latency (s)"),
                color=alt.Color("metric:N", title="Percentile"),
                tooltip=["minute:T", "metric:N", alt.Tooltip("seconds:Q", format=".1f")],
            )
        )
        ref_line = (
            alt.Chart(pd.DataFrame({"y": [60.0]}))
            .mark_rule(color="red", strokeDash=[4, 2])
            .encode(y=alt.Y("y:Q"))
        )
        ref_label = (
            alt.Chart(
                pd.DataFrame({"minute": [df_lat["minute"].max()], "seconds": [64.0]})
            )
            .mark_text(align="right", color="red", fontSize=11)
            .encode(
                x=alt.X("minute:T"),
                y=alt.Y("seconds:Q"),
                text=alt.value("60 s requirement"),
            )
        )
        st.altair_chart(
            (lines + ref_line + ref_label).properties(
                height=280, title="Bronze ingest latency — last 60 minutes"
            ),
            use_container_width=True,
        )
except Exception as _exc:
    st.error(f"Latency chart unavailable: {_exc}")

# Analytics freshness
try:
    df_health = ui_pipeline_health()
    if df_health.empty:
        st.info("No pipeline health data — the analytics job may not have run yet.")
    else:
        row = df_health.iloc[0]
        hc1, hc2 = st.columns(2)
        hc1.metric("Last analytics run", str(row["run_at"])[:19])
        lag = row["cdf_lag_seconds"]
        if pd.notna(lag):
            lag = int(lag)
            hc2.metric(
                "Lakebase last active",
                f"{lag // 60} min {lag % 60} s ago",
                help=(
                    "Time elapsed since the last change was captured from Lakebase. "
                    "Grows during quiet periods — this is normal outside active triage sessions."
                ),
            )
        else:
            hc2.metric("Lakebase last active", "—")
except Exception as _exc:
    st.error(f"Pipeline health unavailable: {_exc}")

st.divider()

# ------------------------------------------------------------------ #
# b. Review queue                                                     #
# ------------------------------------------------------------------ #
st.header("Review queue")

try:
    df_triage = ui_daily_triage()
    if df_triage.empty:
        st.info("No daily triage data — the analytics pipeline may not have run yet.")
    else:
        df_plot = df_triage.sort_values("day")

        # Cases created per day, stacked by tier
        st.altair_chart(
            alt.Chart(df_plot)
            .mark_bar()
            .encode(
                x=alt.X("day:T", title="Day"),
                y=alt.Y("cases_created:Q", title="Cases created"),
                color=alt.Color("tier:N", title="Tier"),
                tooltip=["day:T", "tier:N", "cases_created:Q"],
            )
            .properties(title="Cases created per day by tier", height=220),
            use_container_width=True,
        )

        # Outcomes per day: escalated / resolved / dismissed
        df_out = (
            df_plot.groupby("day")[["escalated", "resolved", "dismissed"]]
            .sum()
            .reset_index()
            .melt(id_vars="day", var_name="outcome", value_name="count")
        )
        st.altair_chart(
            alt.Chart(df_out)
            .mark_bar()
            .encode(
                x=alt.X("day:T", title="Day"),
                y=alt.Y("count:Q", title="Cases"),
                color=alt.Color("outcome:N", title="Outcome"),
                tooltip=["day:T", "outcome:N", "count:Q"],
            )
            .properties(title="Case outcomes per day", height=220),
            use_container_width=True,
        )

        # Median hours to close — latest day that has data
        df_close = df_triage.dropna(subset=["median_hours_to_close"])
        if not df_close.empty:
            best = df_close.sort_values("day").iloc[-1]
            st.metric(
                "Median hours to close (latest day with data)",
                f"{float(best['median_hours_to_close']):.1f} h",
            )
        else:
            st.metric("Median hours to close", "—")
except Exception as _exc:
    st.error(f"Review queue data unavailable: {_exc}")

st.caption(
    "Changes made before Change Data Feed was enabled appear only as their state "
    "at that moment, not as transitions."
)

st.divider()

# ------------------------------------------------------------------ #
# c. Case lifecycle                                                   #
# ------------------------------------------------------------------ #
st.header("Case lifecycle")

try:
    df_trans = ui_case_transitions()
    if df_trans.empty:
        st.info(
            "No transition data yet — cases need to change status at least once "
            "after Change Data Feed was enabled."
        )
    else:
        st.dataframe(
            df_trans.rename(columns={
                "from_status":  "From",
                "to_status":    "To",
                "count":        "Transitions",
                "median_hours": "Median hours in state",
            }),
            use_container_width=True,
            hide_index=True,
        )
except Exception as _exc:
    st.error(f"Case lifecycle data unavailable: {_exc}")

st.divider()

# ------------------------------------------------------------------ #
# d. Agent activity                                                   #
# ------------------------------------------------------------------ #
st.header("Agent activity")

try:
    df_agent = ui_agent_daily()
    if df_agent.empty:
        st.info("No agent activity data yet.")
    else:
        total_calls    = int(df_agent["calls"].sum())
        total_errors   = int(df_agent["errors"].sum())
        # Approximate distinct sessions: max(sessions per tool) per day, summed across days
        total_sessions = int(df_agent.groupby("event_date")["sessions"].max().sum())
        error_rate     = total_errors / total_calls if total_calls else 0.0

        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("Total calls",              f"{total_calls:,}")
        mc2.metric("Conversations (sessions)",  f"{total_sessions:,}")
        mc3.metric("Overall error rate",        f"{error_rate:.1%}")

        # Bar chart: total calls per tool
        df_by_tool = (
            df_agent.groupby("tool_name")["calls"]
            .sum()
            .reset_index()
            .sort_values("calls", ascending=False)
        )
        st.altair_chart(
            alt.Chart(df_by_tool)
            .mark_bar()
            .encode(
                x=alt.X("calls:Q", title="Total calls"),
                y=alt.Y("tool_name:N", sort="-x", title="Tool"),
                tooltip=["tool_name:N", "calls:Q"],
            )
            .properties(
                title="Tool call frequency",
                height=max(140, len(df_by_tool) * 28),
            ),
            use_container_width=True,
        )

        # Per-tool summary table
        df_stats = (
            df_agent.groupby("tool_name")
            .agg(
                calls=("calls", "sum"),
                writes=("writes", "sum"),
                errors=("errors", "sum"),
                avg_latency_ms=("avg_latency_ms", "mean"),
            )
            .reset_index()
            .sort_values("calls", ascending=False)
        )
        df_stats["error_rate"] = df_stats.apply(
            lambda r: f"{r['errors'] / r['calls']:.1%}" if r["calls"] > 0 else "—",
            axis=1,
        )
        df_stats["avg_latency_ms"] = df_stats["avg_latency_ms"].round(0).astype("Int64")
        st.dataframe(
            df_stats[
                ["tool_name", "calls", "writes", "errors", "error_rate", "avg_latency_ms"]
            ].rename(columns={
                "tool_name":      "Tool",
                "calls":          "Calls",
                "writes":         "Writes",
                "errors":         "Errors",
                "error_rate":     "Error rate",
                "avg_latency_ms": "Avg latency (ms)",
            }),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "Write counts include previews (e.g. bulk_dismiss with confirm=false). "
            "Errors include deliberate tests of invalid input."
        )
except Exception as _exc:
    st.error(f"Agent activity data unavailable: {_exc}")
