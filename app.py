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
    """특정 계좌의 가장 최신 파일명과 내용을 반환합니다."""
    try:
        files = repo.get_contents("")
        target_files = [f for f in files if f.name.startswith(account_choice) and f.name.endswith(".txt")]
        
        if not target_files:
            return None, None
            
        # 파일명 기준 역순 정렬 (최신 날짜 파일이 상위로 오도록 처리)
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

def parse_account_data(content):
    """계좌 텍스트 데이터를 파싱하여 DataFrame으로 변환합니다."""
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
                "분류": category, 
                "종목코드": ticker, 
                "종목명": name, 
                "보유주수": shares, 
                "비중(%)": ratio,
                "범례라벨": display_label
            })
    return pd.DataFrame(data)

def parse_target_data(content):
    """Investment_Target.txt 데이터를 파싱합니다."""
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
    """모든 과거 파일의 데이터를 취합하고 목표 비중과의 차이를 계산합니다."""
    try:
        files = repo.get_contents("")
        target_files = [f for f in files if f.name.startswith(account_choice) and f.name.endswith(".txt")]
        
        history_data = []
        for file in target_files:
            content = file.decoded_content.decode("utf-8")
            df = parse_account_data(content)
            if df.empty:
                continue
                
            match = re.search(r'_(\d{6})\.txt', file.name)
            if match:
                date_str = match.group(1)
                date_obj = datetime.strptime(date_str, "%y%m%d").date()
            else:
                date_obj = datetime(2026, 2, 12).date()
                
            df['날짜'] = date_obj
            history_data.append(df)
            
        if not history_data:
            return pd.DataFrame()
            
        combined_df = pd.concat(history_data, ignore_index=True)
        combined_df = pd.merge(combined_df, target_df, on="종목명", how="left")
        combined_df["목표비중(%)"] = combined_df["목표비중(%)"].fillna(0)
        combined_df["목표대비편차(%p)"] = combined_df["비중(%)"] - combined_df["목표비중(%)"]
        
        return combined_df
    except:
        return pd.DataFrame()

# ==========================================
# 2. 메인 화면 구성 및 레이아웃 (탭 분할)
# ==========================================
st.set_page_config(page_title="통합 투자 리밸런싱 대시보드", layout="wide")
st.title("📊 통합 포트폴리오 모니터링 & 리밸런싱")

# GitHub 및 Gemini API 초기화
repo = get_github_repo()
client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

# 전역 데이터 로드 (Investment Target)
target_content = get_file_content(repo, "Investment_Target.txt")
target_df = parse_target_data(target_content)

# 사이드바 제어 요소
st.sidebar.header("투자 설정 제어")
account_choice = st.sidebar.selectbox("대상 계좌 선택", ["연금저축", "종합계좌"])
deposit_amount_manwon = st.sidebar.number_input("추가 입금 금액 (만원 단위)", min_value=0, value=100, step=10)
deposit_amount = deposit_amount_manwon * 10000
run_button = st.sidebar.button("AI 리밸런싱 연산 시작")

# 탭 구성 (요구사항 2 반영)
tab1, tab2, tab3 = st.tabs(["🎯 AI 리밸런싱 결과", "📈 연금저축 현황", "📉 종합계좌 현황"])

# ------------------------------------------
# TAB 1: AI 리밸런싱 결과 탭
# ------------------------------------------
with tab1:
    st.subheader("신규 투자 및 리밸런싱 추천")
    
    if run_button:
        latest_filename, chosen_content = get_latest_file_content(repo, account_choice)
        
        if chosen_content:
            with st.spinner("🔄 실시간 주가 동기화 및 최적 매수 수량 산출 중..."):
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
                            prices_info.append(f"- {ticker}: 가격 조회 불가 (AI 직접 추정 필요)")
                    except:
                        prices_info.append(f"- {ticker}: 가격 조회 불가 (AI 직접 추정 필요)")
                        
                price_info_str = "\n".join(prices_info)

                prompt = f"""
                당신은 전문 포트폴리오 관리자입니다.
                사용자가 {account_choice} 계좌에 {deposit_amount}원을 추가로 입금하여, Investment_Target 파일에 명시된 비중과 최대한 일치하도록 포트폴리오를 리밸런싱하려고 합니다.

                [현재 계좌 상태 ({latest_filename})]
                {chosen_content}

                [목표 비중 (Investment_Target.txt)]
                {target_content}
                
                [실시간 시장 가격 정보]
                {price_info_str}

                다음 단계를 수행하세요:
                1. [실시간 시장 가격 정보]를 반영하여 각 ETF의 1주당 매수 가격을 파악하세요.
                2. 목표 비중에 맞추기 위해 {deposit_amount}원의 예산 내에서 어떤 종목을 몇 주씩 추가 매수해야 하는지 계산하세요.
                3. 매수를 완료했다고 가정하고, 전체 자산을 합산하여 현재 계좌의 '보유 주식 수'와 새로운 '비중(%)'을 업데이트하세요.
                4. 아래 JSON 형식으로만 반환하세요.

                {{
                  "buy_recommendation": [
                    {{"name": "종목명", "price": 10000, "buy_shares": 5, "total_cost": 50000}}
                  ],
                  "updated_account_lines": [
                    "분류 종목코드 종목명 N주 M.MM%"
                  ],
                  "explanation": "리밸런싱 분석 결과 설명"
                }}
                """

                try:
                    response = client.models.generate_content(
                        model='gemini-3.5-flash',
                        contents=prompt,
                        config={"response_mime_type": "application/json"}
                    )
                    result = json.loads(response.text.strip())

                    st.success(f"{account_choice} 연산 완료")
                    st.write(result.get("explanation", ""))
                    
                    rec_df = pd.DataFrame(result.get("buy_recommendation", []))
                    if not rec_df.empty:
                        st.dataframe(rec_df, use_container_width=True)
                        total_spent = rec_df['total_cost'].sum()
                        st.info(f"지출 예산: {total_spent:,}원 | 잔여 현금: {deposit_amount - total_spent:,}원")

                    updated_lines = result.get("updated_account_lines", [])
                    if updated_lines:
                        date_str = datetime.now().strftime("%y%m%d")
                        new_file_name = f"{account_choice}_{date_str}.txt"
                        new_file_content = "\n".join(updated_lines)
                        
                        existing_files = [f.name for f in repo.get_contents("")]
                        if new_file_name not in existing_files:
                            if save_to_github(repo, new_file_name, new_file_content, f"Add {new_file_name} via Streamlit"):
                                st.success(f"✅ 신규 파일 `{new_file_name}`이 저장소에 기록되었습니다. 해당 탭에서 최신 현황을 확인하십시오.")
                        else:
                            st.warning(f"⚠️ 금일자 데이터 파일(`{new_file_name}`)이 이미 존재하여 중복 생성을 방지합니다.")
                except Exception as e:
                    st.error(f"오류 발생: {e}")
        else:
            st.error(f"{account_choice}의 데이터를 불러올 수 없습니다.")
    else:
        st.info("사이드바에서 설정값을 입력한 후 'AI 리밸런싱 연산 시작' 버튼을 클릭하십시오.")

# ------------------------------------------
# TAB 2: 연금저축 현황 탭
# ------------------------------------------
with tab2:
    pension_filename, pension_content = get_latest_file_content(repo, "연금저축")
    
    if pension_content:
        st.subheader(f"연금저축 최신 자산 비중 현황 ({pension_filename})")
        pension_current_df = parse_account_data(pension_content)
        
        col1, col2 = st.columns(2)
        with col1:
            fig_p_pie = px.pie(pension_current_df, values='비중(%)', names='범례라벨', 
                               title="현재 포트폴리오 세부 비중")
            fig_p_pie.update_traces(textposition='inside', textinfo='percent')
            st.plotly_chart(fig_p_pie, use_container_width=True)
            
        with col2:
            pension_hist_df = get_all_historical_data(repo, "연금저축", target_df)
            if not pension_hist_df.empty and len(pension_hist_df['날짜'].unique()) >= 1:
                pension_hist_df = pension_hist_df.sort_values('날짜')
                fig_p_line = px.line(pension_hist_df, x='날짜', y='목표대비편차(%p)', color='종목명', markers=True,
                                     title="목표 비중 대비 편차 추이 (0%p 일치)")
                fig_p_line.add_hline(y=0, line_dash="dash", line_color="gray")
                st.plotly_chart(fig_p_line, use_container_width=True)
            else:
                st.info("누적된 시계열 데이터가 존재하지 않습니다.")
    else:
        st.error("연금저축 데이터를 불러올 수 없습니다.")

# ------------------------------------------
# TAB 3: 종합계좌 현황 탭
# ------------------------------------------
with tab3:
    general_filename, general_content = get_latest_file_content(repo, "종합계좌")
    
    if general_content:
        st.subheader(f"종합계좌 최신 자산 비중 현황 ({general_filename})")
        general_current_df = parse_account_data(general_content)
        
        col3, col4 = st.columns(2)
        with col3:
            fig_g_pie = px.pie(general_current_df, values='비중(%)', names='범례라벨', 
                               title="현재 포트폴리오 세부 비중")
            fig_g_pie.update_traces(textposition='inside', textinfo='percent')
            st.plotly_chart(fig_g_pie, use_container_width=True)
            
        with col4:
            general_hist_df = get_all_historical_data(repo, "종합계좌", target_df)
            if not general_hist_df.empty and len(general_hist_df['날짜'].unique()) >= 1:
                general_hist_df = general_hist_df.sort_values('날짜')
                fig_g_line = px.line(general_hist_df, x='날짜', y='목표대비편차(%p)', color='종목명', markers=True,
                                     title="목표 비중 대비 편차 추이 (0%p 일치)")
                fig_g_line.add_hline(y=0, line_dash="dash", line_color="gray")
                st.plotly_chart(fig_g_line, use_container_width=True)
            else:
                st.info("누적된 시계열 데이터가 존재하지 않습니다.")
    else:
        st.error("종합계좌 데이터를 불러올 수 없습니다.")
