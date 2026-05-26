import streamlit as st
import os
import json
import yfinance as yf
from google import genai
from datetime import datetime
import pandas as pd
import plotly.express as px
from github import Github
import re

# ==========================================
# 1. 데이터 파싱 및 GitHub 연동 함수
# ==========================================
def get_github_repo():
    g = Github(st.secrets["GITHUB_TOKEN"])
    repo = g.get_repo(st.secrets["GITHUB_REPO"])
    return repo

def get_latest_file_content(repo, account_choice):
    try:
        files = repo.get_contents("")
        target_files = [f for f in files if f.name.startswith(account_choice) and f.name.endswith(".txt")]
        
        if not target_files:
            return None, None
            
        target_files.sort(key=lambda x: x.name, reverse=True)
        latest_file = target_files[0]
        content = latest_file.decoded_content.decode("utf-8")
        return latest_file.name, content
    except Exception as e:
        st.error(f"GitHub 파일 로드 중 오류 발생: {e}")
        return None, None

def get_file_content(repo, filename):
    try:
        file = repo.get_contents(filename)
        return file.decoded_content.decode("utf-8")
    except:
        return None

def save_to_github(repo, filename, content, commit_message):
    try:
        repo.create_file(filename, commit_message, content)
        return True
    except Exception as e:
        st.error(f"GitHub 파일 저장 중 오류 발생: {e}")
        return False

def update_github_file(repo, filename, new_content, commit_message, sha):
    try:
        repo.update_file(filename, commit_message, new_content, sha)
        return True
    except Exception as e:
        st.error(f"GitHub 파일 업데이트 중 오류 발생: {e}")
        return False

def parse_account_data(content):
    if not content:
        return pd.DataFrame()
    lines = content.strip().split('\n')
    data = []
    for line in lines:
        cleaned_line = re.sub(r'\\s*', '', line).strip()
        parts = cleaned_line.split()
        if len(parts) >= 4:
            category = parts[0]
            ticker = parts[1]
            if "주" in parts[-2]:
                ratio = float(parts[-1].replace('%', ''))
                shares = parts[-2]
                name = " ".join(parts[2:-2])
            else:
                ratio = float(parts[-1].replace('%', ''))
                shares = "0주"
                name = " ".join(parts[2:-1])
            display_label = f"{name} ({shares}, {ratio}%)"
            data.append({
                "분류": category, "종목코드": ticker, "종목명": name, 
                "보유주수": shares, "비중(%)": ratio, "범례라벨": display_label
            })
    return pd.DataFrame(data)

def parse_target_data(content):
    if not content:
        return pd.DataFrame()
    cleaned_content = re.sub(r'\\s*', '', content)
    lines = cleaned_content.strip().split('\n')
    data = []
    for line in lines:
        ratio_match = re.search(r'([\d.]+)\s*%$', line)
        if ratio_match:
            ratio = float(ratio_match.group(1))
            front = line[:ratio_match.start()].strip()
            front_parts = front.split()
            if len(front_parts) >= 2:
                if front_parts[1].isdigit() and len(front_parts[1]) == 6:
                    name = " ".join(front_parts[2:])
                else:
                    name = " ".join(front_parts[1:])
                data.append({"종목명": name, "목표비중(%)": ratio})
    return pd.DataFrame(data)

def get_all_historical_data(repo, account_choice, target_df):
    try:
        files = repo.get_contents("")
        target_files = [f for f in files if f.name.startswith(account_choice) and f.name.endswith(".txt")]
        history_data = []
        for file in target_files:
            content = file.decoded_content.decode("utf-8")
            df = parse_account_data(content)
            if df.empty: continue
            match = re.search(r'_(\d{6})(?:_\d{6})?\.txt', file.name)
            date_obj = datetime.strptime(match.group(1), "%y%m%d").date() if match else datetime(2026, 2, 12).date()
            df['날짜'] = date_obj
            history_data.append(df)
        
        if not history_data: return pd.DataFrame()
        combined_df = pd.concat(history_data, ignore_index=True)
        combined_df = pd.merge(combined_df, target_df, on="종목명", how="left")
        combined_df["목표비중(%)"] = combined_df["목표비중(%)"].fillna(0)
        combined_df["목표대비편차(%p)"] = combined_df["비중(%)"] - combined_df["목표비중(%)"]
        return combined_df
    except:
        return pd.DataFrame()

def df_to_markdown_table(df):
    if df.empty: return "매수 내역 없음"
    header = "| " + " | ".join(df.columns) + " |"
    separator = "|" + "|".join(["---"] * len(df.columns)) + "|"
    rows = []
    for _, row in df.iterrows():
        rows.append("| " + " | ".join(str(x) for x in row.values) + " |")
    return "\n".join([header, separator] + rows)

def append_rebalancing_history(repo, account, deposit, spent, rec_df, explanation):
    filename = "Rebalancing_History.md"
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    remain = deposit - spent
    md_table = df_to_markdown_table(rec_df)

    # HTML 태그 대신 마크다운 렌더링에 영향받지 않는 특수 기호 사용
    new_record = f"""
@@@ RECORD_START @@@
### 🕒 {current_time} | 계좌: {account}
- **추가 입금액**: {deposit:,}원
- **지출 예산**: {spent:,}원
- **잔여 현금**: {remain:,}원

#### 🛒 매수 추천 내역
{md_table}

#### 💡 AI 분석 결과
{explanation}
@@@ RECORD_END @@@
"""
    try:
        file = repo.get_contents(filename)
        existing_content = file.decoded_content.decode("utf-8")
        updated_content = new_record.strip() + "\n\n" + existing_content
        update_github_file(repo, filename, updated_content, f"Append history: {current_time}", file.sha)
    except:
        save_to_github(repo, filename, new_record.strip(), f"Create history file: {current_time}")

# ==========================================
# 2. 메인 화면 구성 및 레이아웃
# ==========================================
st.set_page_config(page_title="통합 투자 리밸런싱 대시보드", layout="wide")
st.title("📊 통합 포트폴리오 모니터링 & 리밸런싱")

repo = get_github_repo()
client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

target_content = get_file_content(repo, "Investment_Target.txt")
target_df = parse_target_data(target_content)

st.sidebar.header("투자 설정 제어")
account_choice = st.sidebar.selectbox("대상 계좌 선택", ["연금저축", "종합계좌"])
deposit_amount_manwon = st.sidebar.number_input("추가 입금 금액 (만원 단위)", min_value=0, value=100, step=10)
deposit_amount = deposit_amount_manwon * 10000
run_button = st.sidebar.button("AI 리밸런싱 연산 시작")

tab1, tab2, tab3, tab4 = st.tabs(["🎯 AI 리밸런싱 결과", "📈 연금저축 현황", "📉 종합계좌 현황", "📝 리밸런싱 이력"])

# ------------------------------------------
# TAB 1: AI 리밸런싱 결과
# ------------------------------------------
with tab1:
    st.subheader("신규 투자 및 리밸런싱 추천")
    
    if run_button:
        latest_filename, chosen_content = get_latest_file_content(repo, account_choice)
        if chosen_content:
            with st.spinner("🔄 연산 중..."):
                all_text = chosen_content + "\n" + target_content
                tickers = set(re.findall(r"\b\d{6}\b", all_text))
                prices_info = []
                for ticker in tickers:
                    try:
                        stock = yf.Ticker(f"{ticker}.KS")
                        hist = stock.history(period="1d")
                        if not hist.empty:
                            price = int(hist['Close'].iloc[-1])
                            prices_info.append(f"- {ticker}: {price}원")
                        else:
                            prices_info.append(f"- {ticker}: 가격 조회 불가 (추정 요망)")
                    except:
                        prices_info.append(f"- {ticker}: 가격 조회 불가 (추정 요망)")
                        
                prompt = f"""
                당신은 전문 포트폴리오 관리자입니다.
                사용자가 {account_choice} 계좌에 {deposit_amount}원을 추가 입금하여, Investment_Target 파일 명시 비중과 일치하도록 리밸런싱을 원합니다.

                [현재 계좌 ({latest_filename})]
                {chosen_content}
                [목표 비중]
                {target_content}
                [실시간 가격]
                {chr(10).join(prices_info)}

                다음 단계를 수행하세요:
                1. 실시간 가격을 반영하여 종목별 1주당 매수 가격 파악.
                2. 예산 {deposit_amount}원 내 추가 매수 수량 계산.
                3. 전체 자산 합산 후 보유 주식 수 및 비중(%) 업데이트.
                4. 아래 JSON 형식만 반환.

                {{
                  "buy_recommendation": [{{"name": "종목명", "price": 10000, "buy_shares": 5, "total_cost": 50000}}],
                  "updated_account_lines": ["분류 종목코드 종목명 N주 M.MM%"],
                  "explanation": "설명"
                }}
                """
                try:
                    res = client.models.generate_content(
                        model='gemini-3.5-flash', contents=prompt, config={"response_mime_type": "application/json"}
                    )
                    result = json.loads(res.text.strip())

                    st.success(f"{account_choice} 연산 완료")
                    st.write(result.get("explanation", ""))
                    
                    rec_df = pd.DataFrame(result.get("buy_recommendation", []))
                    total_spent = 0
                    if not rec_df.empty:
                        st.dataframe(rec_df, use_container_width=True)
                        total_spent = rec_df['total_cost'].sum()
                        st.info(f"지출 예산: {total_spent:,}원 | 잔여 현금: {deposit_amount - total_spent:,}원")

                    updated_lines = result.get("updated_account_lines", [])
                    if updated_lines:
                        date_str = datetime.now().strftime("%y%m%d_%H%M%S")
                        new_file_name = f"{account_choice}_{date_str}.txt"
                        save_to_github(repo, new_file_name, "\n".join(updated_lines), f"Add {new_file_name}")
                        st.success(f"✅ `{new_file_name}` 저장소 백업 완료. (시계열에 별도 데이터로 누적됩니다)")

                    append_rebalancing_history(repo, account_choice, deposit_amount, total_spent, rec_df, result.get("explanation", ""))
                    st.success("✅ `Rebalancing_History.md` 파일에 실행 이력이 성공적으로 누적되었습니다.")
                except Exception as e:
                    st.error(f"오류 발생: {e}")
        else:
            st.error(f"{account_choice} 데이터를 불러올 수 없습니다.")
    else:
        st.info("조건 설정 후 연산을 시작하십시오.")

# ------------------------------------------
# TAB 2: 연금저축 현황
# ------------------------------------------
with tab2:
    p_file, p_cont = get_latest_file_content(repo, "연금저축")
    if p_cont:
        st.subheader(f"최신 자산 비중 ({p_file})")
        col1, col2 = st.columns(2)
        with col1:
            fig_p = px.pie(parse_account_data(p_cont), values='비중(%)', names='범례라벨', title="포트폴리오 비중")
            fig_p.update_traces(textposition='inside', textinfo='percent')
            st.plotly_chart(fig_p, use_container_width=True)
        with col2:
            p_hist = get_all_historical_data(repo, "연금저축", target_df)
            if not p_hist.empty and len(p_hist['날짜'].unique()) > 0:
                p_hist = p_hist.sort_values('날짜')
                fig_pl = px.line(p_hist, x='날짜', y='목표대비편차(%p)', color='종목명', markers=True, title="목표 비중 편차 (0%p 일치)")
                fig_pl.add_hline(y=0, line_dash="dash", line_color="gray")
                st.plotly_chart(fig_pl, use_container_width=True)
            else: st.info("시계열 데이터 없음.")

# ------------------------------------------
# TAB 3: 종합계좌 현황
# ------------------------------------------
with tab3:
    g_file, g_cont = get_latest_file_content(repo, "종합계좌")
    if g_cont:
        st.subheader(f"최신 자산 비중 ({g_file})")
        col3, col4 = st.columns(2)
        with col3:
            fig_g = px.pie(parse_account_data(g_cont), values='비중(%)', names='범례라벨', title="포트폴리오 비중")
            fig_g.update_traces(textposition='inside', textinfo='percent')
            st.plotly_chart(fig_g, use_container_width=True)
        with col4:
            g_hist = get_all_historical_data(repo, "종합계좌", target_df)
            if not g_hist.empty and len(g_hist['날짜'].unique()) > 0:
                g_hist = g_hist.sort_values('날짜')
                fig_gl = px.line(g_hist, x='날짜', y='목표대비편차(%p)', color='종목명', markers=True, title="목표 비중 편차 (0%p 일치)")
                fig_gl.add_hline(y=0, line_dash="dash", line_color="gray")
                st.plotly_chart(fig_gl, use_container_width=True)
            else: st.info("시계열 데이터 없음.")

# ------------------------------------------
# TAB 4: 리밸런싱 이력
# ------------------------------------------
with tab4:
    st.subheader("실행 결과 이력 조회")
    history_content = get_file_content(repo, "Rebalancing_History.md")
    
    if history_content:
        # 수정된 일반 텍스트 기호 기준으로 파싱
        parts = history_content.split("@@@ RECORD_START @@@")
        records = [p.split("@@@ RECORD_END @@@")[0].strip() for p in parts if "@@@ RECORD_END @@@" in p]
        
        if records:
            for idx, record in enumerate(records):
                title_match = re.search(r'###\s*(.*?)\n', record)
                expander_title = title_match.group(1).strip() if title_match else f"이력 {len(records) - idx}"
                
                with st.expander(expander_title, expanded=(idx == 0)):
                    st.markdown(record)
        else:
            st.info("이력 형식을 파싱할 수 없거나 기록이 비어 있습니다.")
    else:
        st.info("아직 저장된 리밸런싱 이력이 없습니다.")
