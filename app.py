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
# 1. GitHub 연동 및 파일 처리 함수
# ==========================================
def get_github_repo():
    g = Github(st.secrets["GITHUB_TOKEN"])
    repo = g.get_repo(st.secrets["GITHUB_REPO"]) # 형식: username/repo_name
    return repo

def get_latest_file_content(repo, account_choice):
    """특정 계좌의 가장 최신 파일 내용을 가져옵니다."""
    files = repo.get_contents("")
    target_files = [f for f in files if f.name.startswith(account_choice) and f.name.endswith(".txt")]
    
    if not target_files:
        return None, None
        
    # 이름 기준 정렬 (연금저축.txt -> 연금저축_YYMMDD.txt 순으로 최신 정렬됨)
    target_files.sort(key=lambda x: x.name, reverse=True)
    latest_file = target_files[0]
    content = latest_file.decoded_content.decode("utf-8")
    return latest_file.name, content

def get_file_content(repo, filename):
    try:
        file = repo.get_contents(filename)
        return file.decoded_content.decode("utf-8")
    except:
        return None

def save_to_github(repo, filename, content, commit_message):
    """새로운 파일을 GitHub 저장소에 커밋합니다."""
    repo.create_file(filename, commit_message, content)

def parse_account_data(content):
    """텍스트 데이터를 파싱하여 DataFrame으로 변환합니다."""
    lines = content.strip().split('\n')
    data = []
    for line in lines:
        parts = line.strip().split()
        if len(parts) >= 4:
            category = parts[0]
            ticker = parts[1]
            # 주식수가 있는 경우와 없는 경우(Target 파일) 분리 처리
            if "주" in parts[-2]:
                ratio = float(parts[-1].replace('%', ''))
                shares = parts[-2]
                name = " ".join(parts[2:-2])
            else:
                ratio = float(parts[-1].replace('%', ''))
                shares = "0주"
                name = " ".join(parts[2:-1])
            data.append({"분류": category, "종목코드": ticker, "종목명": name, "비중(%)": ratio})
    return pd.DataFrame(data)

def get_all_historical_data(repo, account_choice):
    """시계열 차트를 위해 저장소 내 모든 계좌 이력 파일을 파싱합니다."""
    files = repo.get_contents("")
    target_files = [f for f in files if f.name.startswith(account_choice) and f.name.endswith(".txt")]
    
    history_data = []
    for file in target_files:
        content = file.decoded_content.decode("utf-8")
        df = parse_account_data(content)
        
        # 파일명에서 날짜 추출 (예: 연금저축_240526.txt)
        match = re.search(r'_(\d{6})\.txt', file.name)
        if match:
            date_str = match.group(1)
            date_obj = datetime.strptime(date_str, "%y%m%d").date()
        else:
            date_obj = datetime(2023, 1, 1).date() # 기본 원본 파일용 가상 날짜
            
        df['날짜'] = date_obj
        history_data.append(df)
        
    if history_data:
        return pd.concat(history_data, ignore_index=True)
    return pd.DataFrame()

# ==========================================
# 2. 메인 Streamlit UI
# ==========================================
st.set_page_config(page_title="투자 리밸런싱 대시보드", layout="wide")
st.title("📊 투자 포트폴리오 관리 및 리밸런싱")

# API Client 초기화
client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
repo = get_github_repo()

# 사이드바 입력
st.sidebar.header("리밸런싱 설정")
account_choice = st.sidebar.selectbox("계좌 선택", ["연금저축", "종합계좌"])
deposit_amount_manwon = st.sidebar.number_input("추가 입금액 (만원 단위)", min_value=0, value=100, step=10)
deposit_amount = deposit_amount_manwon * 10000
run_button = st.sidebar.button("리밸런싱 AI 실행")

# 데이터 로드
latest_filename, account_data = get_latest_file_content(repo, account_choice)
target_data = get_file_content(repo, "Investment_Target.txt")

if not latest_filename or not target_data:
    st.error("GitHub 저장소에서 계좌 데이터 또는 Investment_Target.txt 파일을 찾을 수 없습니다.")
    st.stop()

# ==========================================
# 3. 대시보드 시각화 (파이 차트 & 시계열 차트)
# ==========================================
st.subheader(f"현재 자산 비중 현황 ({latest_filename})")

col1, col2 = st.columns(2)
current_df = parse_account_data(account_data)

with col1:
    fig_pie = px.pie(current_df, values='비중(%)', names='종목명', title=f"{account_choice} 현재 포트폴리오 비중")
    fig_pie.update_traces(textposition='inside', textinfo='percent+label')
    st.plotly_chart(fig_pie, use_container_width=True)

with col2:
    hist_df = get_all_historical_data(repo, account_choice)
    if not hist_df.empty and len(hist_df['날짜'].unique()) > 1:
        hist_df = hist_df.sort_values('날짜')
        fig_line = px.line(hist_df, x='날짜', y='비중(%)', color='종목명', markers=True, title=f"{account_choice} 종목별 비중 변화 추이")
        st.plotly_chart(fig_line, use_container_width=True)
    else:
        st.info("시계열 차트를 그리기 위한 과거 데이터(YYMMDD 파일)가 충분하지 않습니다.")

st.divider()

# ==========================================
# 4. 리밸런싱 AI 실행
# ==========================================
if run_button:
    with st.spinner("🔍 실시간 시장 데이터 조회 및 AI 분석 중..."):
        # 실시간 가격 조회
        all_text = account_data + "\n" + target_data
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

        # 프롬프트 생성
        prompt = f"""
        당신은 전문 포트폴리오 관리자입니다.
        사용자가 {account_choice} 계좌에 {deposit_amount}원을 추가로 입금하여, Investment_Target 파일에 명시된 비중과 최대한 일치하도록 포트폴리오를 리밸런싱하려고 합니다.

        [현재 계좌 상태 ({latest_filename})]
        {account_data}

        [목표 비중 (Investment_Target.txt)]
        {target_data}
        
        [실시간 시장 가격 정보]
        {price_info_str}

        다음 단계를 수행하세요:
        1. [실시간 시장 가격 정보]를 반영하여 각 ETF의 1주당 매수 가격을 파악하세요.
        2. 목표 비중에 맞추기 위해 {deposit_amount}원의 예산 내에서 종목별 추가 매수 수량을 계산하세요.
        3. 전체 자산을 합산하여 현재 계좌의 '보유 주식 수'와 새로운 '비중(%)'을 업데이트하세요.
        4. 아래 JSON 형식으로만 반환하세요.

        {{
          "buy_recommendation": [
            {{"name": "종목명", "price": 10000, "buy_shares": 5, "total_cost": 50000}}
          ],
          "updated_account_lines": [
            "주식형 294400 KIWOOM 200TR 5주 30.5%"
          ],
          "explanation": "리밸런싱 분석 결과 설명"
        }}
        """

        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )
            result = json.loads(response.text.strip())

            # 결과 출력
            st.subheader("💡 AI 리밸런싱 추천 결과")
            st.write(result.get("explanation", ""))
            
            recommendation_df = pd.DataFrame(result.get("buy_recommendation", []))
            if not recommendation_df.empty:
                st.dataframe(recommendation_df, use_container_width=True)
                total_spent = recommendation_df['total_cost'].sum()
                st.write(f"**총 예상 투자 금액**: {total_spent:,}원 / **남은 현금**: {deposit_amount - total_spent:,}원")

            # GitHub에 새 파일 저장
            updated_lines = result.get("updated_account_lines", [])
            if updated_lines:
                date_str = datetime.now().strftime("%y%m%d")
                new_file_name = f"{account_choice}_{date_str}.txt"
                new_file_content = "\n".join(updated_lines)
                
                # 동일한 파일명이 이미 존재하는지 확인 후 저장
                existing_files = [f.name for f in repo.get_contents("")]
                if new_file_name not in existing_files:
                    save_to_github(repo, new_file_name, new_file_content, f"Add {new_file_name} via Streamlit")
                    st.success(f"✅ 성공적으로 `{new_file_name}` 파일이 GitHub 저장소에 생성 및 저장되었습니다.")
                else:
                    st.info(f"ℹ️ 금일자 파일(`{new_file_name}`)이 이미 존재합니다. 덮어쓰지 않았습니다.")

        except Exception as e:
            st.error(f"실행 중 오류가 발생했습니다: {e}")
