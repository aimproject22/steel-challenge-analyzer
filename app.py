# app.py
# -*- coding: utf-8 -*-

import re
from io import BytesIO

import pandas as pd
import streamlit as st

from parser import parse_docx
from db_utils import (
    init_db,
    save_to_db,
    load_runs_df,
    load_logs_df,
    reset_database,
)
from ml_engine import (
    train_score_model,
    generate_recommendations,
    estimate_uncertainty,
)


def remove_illegal_characters(value):
    if isinstance(value, str):
        value = re.sub(
            r"[\x00-\x08\x0B-\x0C\x0E-\x1F]",
            "",
            value
        )

    return value


def clean_dataframe_for_excel(df):
    if df.empty:
        return df

    return df.applymap(remove_illegal_characters)


def make_excel(runs_df, logs_df):
    runs_df = clean_dataframe_for_excel(runs_df.copy())
    logs_df = clean_dataframe_for_excel(logs_df.copy())

    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        runs_df.to_excel(
            writer,
            sheet_name="Results_and_Features",
            index=False
        )

        logs_df.to_excel(
            writer,
            sheet_name="Event_Log",
            index=False
        )

    output.seek(0)
    return output


def to_numeric_col(df, col):
    if col in df.columns:
        return pd.to_numeric(df[col], errors="coerce")
    return pd.Series(dtype="float64")


def make_histogram_df(series, bin_size=10, label="Value"):
    clean = pd.to_numeric(series, errors="coerce").dropna()

    if clean.empty:
        return pd.DataFrame(columns=[label, "Count"])

    binned = (clean / bin_size).round(0) * bin_size

    hist_df = (
        binned
        .value_counts()
        .sort_index()
        .reset_index()
    )

    hist_df.columns = [label, "Count"]
    return hist_df


st.set_page_config(
    page_title="Steel Challenge DOCX Analyzer",
    layout="wide"
)

init_db()

st.title("Steel Challenge AI 공정 분석 플랫폼")
st.caption("DOCX 업로드 → Supabase DB 저장 → Feature 추출 → ML 학습 → 공정 추천 → 시각화")

uploader = st.text_input("업로드한 사람 이름", placeholder="예: 하준영")

uploaded_files = st.file_uploader(
    "Steel Challenge 결과 DOCX 파일 업로드",
    type=["docx"],
    accept_multiple_files=True
)

if st.button("업로드 파일 저장"):
    if not uploader:
        st.warning("업로드한 사람 이름을 입력하세요.")
    elif not uploaded_files:
        st.warning("DOCX 파일을 업로드하세요.")
    else:
        success_count = 0

        for uploaded_file in uploaded_files:
            try:
                data, logs = parse_docx(uploaded_file)
                save_to_db(uploader, uploaded_file.name, data, logs)
                success_count += 1
            except Exception as e:
                st.error(f"{uploaded_file.name} 처리 실패: {e}")

        if success_count > 0:
            st.success(f"{success_count}개 파일 저장 완료")
            st.rerun()


st.divider()

runs_df = load_runs_df()
logs_df = load_logs_df()

if runs_df.empty:
    st.info("아직 저장된 결과가 없습니다.")
else:
    feature_cols = [
        c for c in runs_df.columns
        if str(c).startswith("feature >")
    ]

    base_cols = [
        c for c in runs_df.columns
        if not str(c).startswith("feature >")
    ]

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        ["전체 결과", "ML Feature", "Event Log", "ML 추천", "시각화"]
    )

    with tab1:
        st.subheader("전체 업로드 결과")
        st.dataframe(runs_df[base_cols], use_container_width=True)

    with tab2:
        st.subheader("ML용 Feature 테이블")

        if feature_cols:
            view_cols = [
                "Run ID",
                "Uploader",
                "File Name",
                "Score",
                "Cost Per Tonne",
            ] + feature_cols

            existing_cols = [
                c for c in view_cols
                if c in runs_df.columns
            ]

            st.dataframe(
                runs_df[existing_cols],
                use_container_width=True
            )
        else:
            st.info("아직 추출된 feature가 없습니다.")

    with tab3:
        st.subheader("Event Log 전체 기록")

        if logs_df.empty:
            st.info("아직 Event Log가 없습니다.")
        else:
            st.dataframe(logs_df, use_container_width=True)

    with tab4:
        st.subheader("ML 기반 공정 추천")

        model, used_features, importance_df, metrics = train_score_model(runs_df)

        if model is None:
            st.warning(metrics.get("error", "모델 학습 실패"))
        else:
            col1, col2 = st.columns(2)

            with col1:
                st.markdown("### 모델 성능")
                st.json(metrics)

            with col2:
                st.markdown("### 데이터 개수")
                st.metric("Total Runs", len(runs_df))
                st.metric("Feature Count", len(used_features))

            st.markdown("### Score 영향 Feature Importance")
            st.dataframe(
                importance_df.head(20),
                use_container_width=True
            )

            importance_chart_df = importance_df.head(15).copy()
            if not importance_chart_df.empty:
                st.bar_chart(
                    importance_chart_df,
                    x="Feature",
                    y="Importance",
                    use_container_width=True
                )

            st.markdown("### 추천 공정 변화 방향")
            recommendations = generate_recommendations(
                runs_df,
                importance_df
            )

            if recommendations:
                rec_df = pd.DataFrame(recommendations)
                st.dataframe(rec_df, use_container_width=True)

                st.markdown("### 요약 추천")
                for rec in recommendations[:5]:
                    st.write(f"- {rec['Comment']}")
            else:
                st.info("추천을 생성할 수 없습니다.")

            st.markdown("### 불확실성이 큰 Run")
            uncertainty_df = estimate_uncertainty(
                model,
                runs_df,
                used_features
            )

            if uncertainty_df is not None:
                st.dataframe(
                    uncertainty_df.head(20),
                    use_container_width=True
                )

                uncertainty_chart_df = uncertainty_df.head(20).copy()
                st.bar_chart(
                    uncertainty_chart_df,
                    x="File Name",
                    y="Uncertainty",
                    use_container_width=True
                )

            st.info(
                "현재 추천은 fake data 기반 파이프라인 검증용입니다. "
                "실제 공정 추천은 실제 데이터가 충분히 누적된 후 신뢰할 수 있습니다."
            )

    with tab5:
        st.subheader("데이터 시각화")

        plot_df = runs_df.copy()

        plot_df["Score"] = to_numeric_col(plot_df, "Score")
        plot_df["Cost Per Tonne"] = to_numeric_col(plot_df, "Cost Per Tonne")

        c1, c2, c3 = st.columns(3)

        with c1:
            st.metric(
                "Total Runs",
                len(plot_df)
            )

        with c2:
            st.metric(
                "Average Score",
                f"{plot_df['Score'].mean():.2f}"
            )

        with c3:
            st.metric(
                "Average Cost Per Tonne",
                f"{plot_df['Cost Per Tonne'].mean():.2f}"
            )

        st.markdown("### Score 분포")
        score_hist = make_histogram_df(
            plot_df["Score"],
            bin_size=10,
            label="Score"
        )

        if score_hist.empty:
            st.info("Score 데이터가 부족합니다.")
        else:
            st.bar_chart(
                score_hist,
                x="Score",
                y="Count",
                use_container_width=True
            )

        st.markdown("### Cost Per Tonne 분포")
        cost_hist = make_histogram_df(
            plot_df["Cost Per Tonne"],
            bin_size=50,
            label="Cost Per Tonne"
        )

        if cost_hist.empty:
            st.info("Cost Per Tonne 데이터가 부족합니다.")
        else:
            st.bar_chart(
                cost_hist,
                x="Cost Per Tonne",
                y="Count",
                use_container_width=True
            )

        st.markdown("### Score vs Cost Per Tonne")

        scatter_cols = [
            c for c in ["Score", "Cost Per Tonne", "Uploader", "File Name"]
            if c in plot_df.columns
        ]

        scatter_df = plot_df[scatter_cols].dropna(
            subset=["Score", "Cost Per Tonne"]
        )

        if scatter_df.empty:
            st.info("산점도를 그릴 데이터가 부족합니다.")
        else:
            st.scatter_chart(
                scatter_df,
                x="Cost Per Tonne",
                y="Score",
                use_container_width=True
            )

        st.markdown("### Steel Grade별 평균 Score")

        if "Steel Grade" in plot_df.columns:
            grade_score_df = (
                plot_df
                .dropna(subset=["Steel Grade", "Score"])
                .groupby("Steel Grade", as_index=False)["Score"]
                .mean()
                .sort_values("Score", ascending=False)
            )

            if not grade_score_df.empty:
                st.bar_chart(
                    grade_score_df,
                    x="Steel Grade",
                    y="Score",
                    use_container_width=True
                )
            else:
                st.info("Steel Grade별 Score 데이터가 부족합니다.")
        else:
            st.info("Steel Grade 컬럼이 없습니다.")

        st.markdown("### 주요 Feature와 Score 관계")

        if feature_cols:
            selected_feature = st.selectbox(
                "Score와 비교할 Feature 선택",
                feature_cols
            )

            temp_df = plot_df[
                ["Score", selected_feature]
            ].copy()

            temp_df[selected_feature] = pd.to_numeric(
                temp_df[selected_feature],
                errors="coerce"
            )

            temp_df = temp_df.dropna()

            if temp_df.empty:
                st.info("선택한 Feature와 Score를 비교할 데이터가 부족합니다.")
            else:
                st.scatter_chart(
                    temp_df,
                    x=selected_feature,
                    y="Score",
                    use_container_width=True
                )
        else:
            st.info("Feature 컬럼이 아직 없습니다.")

        st.markdown("### Score 상위 Run")

        display_cols = [
            "Run ID",
            "Uploader",
            "File Name",
            "Score",
            "Cost Per Tonne",
            "Steel Grade",
        ]

        existing_display_cols = [
            c for c in display_cols
            if c in plot_df.columns
        ]

        top_df = plot_df.sort_values(
            "Score",
            ascending=False
        ).head(20)

        st.dataframe(
            top_df[existing_display_cols],
            use_container_width=True
        )

    excel_file = make_excel(runs_df, logs_df)

    st.download_button(
        label="전체 통합 엑셀 다운로드",
        data=excel_file,
        file_name="steel_challenge_all_results_with_features.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


st.divider()

with st.expander("관리자용: 전체 데이터 초기화"):
    password = st.text_input("관리자 비밀번호", type="password")

    if st.button("DB 전체 초기화"):
        if password == "1234":
            reset_database()
            st.success("DB 초기화 완료")
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")