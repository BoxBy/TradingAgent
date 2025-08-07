import pandas as pd
import requests
import os
import io
import urllib
import ssl
import zipfile

from .. import config
from ..utils import logger

log = logger.get_logger(__name__)

def scrape_and_save_nasdaq_tickers():
    """NASDAQ 공식 FTP 서버에서 전체 종목 리스트를 다운로드하여 CSV로 저장합니다."""
    log.info("Fetching NASDAQ ticker list...")
    file_path = os.path.join(config.DATA_DIR, 'nasdaq_tickers.csv')
    try:
        url = "ftp://ftp.nasdaqtrader.com/symboldirectory/nasdaqlisted.txt"
        df = pd.read_csv(url, sep='|')
        df = df[:-1]
        
        df = df[df['Test Issue'] == 'N']
        # ✨ 컬럼명 'Financial Status / In Deficient' -> 'Financial Status'로 수정
        # 이 필드가 없는 경우도 대비하여 try-except 구문 추가
        if 'Financial Status' in df.columns:
            df = df[df['Financial Status'] == 'N']
        
        df = df[['Symbol', 'Security Name']]
        df.to_csv(file_path, index=False)
        log.info(f"Successfully saved {len(df)} NASDAQ tickers to {file_path}")
    except Exception as e:
        log.error(f"Failed to fetch NASDAQ tickers: {e}", exc_info=True)
        if not os.path.exists(file_path):
            log.warning("Using fallback NASDAQ ticker list.")
            fallback_df = pd.DataFrame({'Symbol': ['AAPL', 'MSFT', 'GOOGL'], 'Security Name': ['Apple Inc.', 'Microsoft Corporation', 'Alphabet Inc.']})
            fallback_df.to_csv(file_path, index=False)
            
def get_kospi_tickers():
    """
    대신증권 서버에서 KOSPI 종목 마스터 파일을 직접 다운로드하여
    DataFrame으로 반환합니다. 가장 안정적인 방법입니다.
    """
    log.info("Fetching KOSPI master file from Daishin Securities server...")
    # SSL 인증서 검증을 건너뛰어 다운로드 오류를 방지합니다.
    ssl._create_default_https_context = ssl._create_unverified_context

    # 1. 마스터 파일을 메모리로 직접 다운로드합니다.
    url = "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"
    with urllib.request.urlopen(url) as response:
        zip_content = response.read()

    # 2. 메모리 상에서 ZIP 파일의 압축을 풉니다.
    with zipfile.ZipFile(io.BytesIO(zip_content)) as kospi_zip:
        # ZIP 파일 안의 .mst 파일 내용을 읽습니다.
        mst_content = kospi_zip.read('kospi_code.mst')

    # 3. .mst 파일(고정 너비 텍스트)을 파싱하여 CSV 형식으로 변환합니다.
    #    이 파일은 'cp949' 인코딩을 사용합니다.
    decoded_content = mst_content.decode('euc-kr')
    
    
    parsed_lines = []
    for row in decoded_content.splitlines():
        # 표준 코드는 10번째 글자부터 12자리입니다.
        isin_code = row[9:21].strip()

        # 'KR'로 시작하고, 3번째 글자가 '7'(주식)인 항목만 필터링합니다.
            # 이렇게 하면 펀드, ETF 등 다른 증권은 모두 걸러집니다.
        if isin_code.startswith('KR') and len(isin_code) == 12 and isin_code[2] == '7':
            # 6자리 종목 코드는 ISIN의 4번째부터 9번째까지입니다.
            ticker = isin_code[3:9]
            name_field = row[21:69]
            name = name_field.split()[0].strip()
            parsed_lines.append({'종목코드': ticker, '종목명': name})

    # 4. 파싱된 데이터를 DataFrame으로 변환합니다.
    if not parsed_lines:
        raise ValueError("Could not parse any stock tickers from the master file.")

    df = pd.DataFrame(parsed_lines)
    return df

def get_nasdaq_tickers():
    """
    (기존과 동일) NASDAQ 종목 리스트를 스크레이핑합니다.
    """
    log.info("Fetching NASDAQ ticker list...")
    # ... (이 함수는 기존 코드를 그대로 사용하시면 됩니다) ...
    url = 'https://www.nasdaq.com/market-activity/stocks/screener'
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        r = requests.get(url, headers=headers)
        r.raise_for_status()
        df = pd.read_html(r.text)[0]
        df = df[['Symbol', 'Name']]
        df.rename(columns={'Symbol': '종목코드', 'Name': '종목명'}, inplace=True)
        return df
    except Exception as e:
        log.error(f"Failed to fetch NASDAQ tickers: {e}")
        return pd.DataFrame()

def main():
    log.info("Starting stock list scraping...")
    
    # Scrape and save NASDAQ tickers
    scrape_and_save_nasdaq_tickers()

    # Scrape and save KOSPI tickers
    try:
        kospi_df = get_kospi_tickers()
        if not kospi_df.empty:
            file_path = os.path.join(config.DATA_DIR, 'kospi_tickers.csv')
            kospi_df.to_csv(file_path, index=False)
            log.info(f"Successfully saved {len(kospi_df)} KOSPI tickers to {file_path}")
        else:
            log.warning("KOSPI ticker DataFrame is empty. No KOSPI tickers saved.")
    except Exception as e:
        log.error(f"Failed to fetch KOSPI tickers: {e}", exc_info=True)

if __name__ == '__main__':
    main()