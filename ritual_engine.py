import os
import json
import random
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List

from dotenv import load_dotenv
from google import genai
from google.genai import types
from notion_client import Client
from pydantic import BaseModel, Field
import feedparser
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# 초기 설정
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
load_dotenv()

# 환경 변수 확인
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

if not all([GEMINI_API_KEY, NOTION_TOKEN, NOTION_DATABASE_ID]):
    logging.warning("필수 환경 변수가 누락되었습니다. .env 파일을 확인해주세요.")

# 클라이언트 초기화
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
notion_client = Client(auth=NOTION_TOKEN) if NOTION_TOKEN else None


# ---------------------------------------------------------------------------
# 1. Pydantic 스키마 정의 (LLM 출력 강제화)
# ---------------------------------------------------------------------------
class ReferenceLink(BaseModel):
    url: str = Field(description="실제로 참고하거나 영감을 얻은 기사의 원문 URL")
    comment: str = Field(description="이 기사의 어떤 내용을 아티클 작성에 활용했는지 한 줄 요약")


class EditorialContent(BaseModel):
    kakao_teaser: str = Field(description="카카오톡 프리뷰용 후킹 티저")
    web_article: str = Field(description="본문 아티클 (마크다운 포맷)")
    editor_note: str = Field(description="AI 에디터의 기획 의도, 선택한 소스에 대한 팩트체크 및 작성 논리를 설명하는 노트")
    reference_links: List[ReferenceLink] = Field(description="실제로 아티클 작성에 활용된 참고 소스 링크 및 활용 코멘트 목록")
    visual_prompt: str = Field(description="미드저니 등 이미지 생성을 위한 영문 화보 프롬프트 (영어), 하이엔드 매거진 스타일(35mm 렌즈, 자연광, 미니멀리즘 등).")


# ---------------------------------------------------------------------------
# 2. 페르소나 및 시스템 프롬프트 정의
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """
당신은 글로벌 웰니스 매거진(예: Monocle, Kinfolk)의 편집장(Editor-in-Chief)입니다.
'WYSH'라는 브랜드를 단순한 식품 브랜드를 넘어, 전 세계 웰니스 라이프스타일을 선도하는 지적인 미디어로 만드십시오.

[Persona & Tone]
- 세련된(Sophisticated), 통찰력 있는(Insightful), 감각적인(Trendy).
- 독자가 매주 아침 '나를 위한 영감'으로서 이 메시지를 기다리게 만드는 것이 목표입니다.
- '다이어트' 대신 '신체 최적화/퍼포먼스 지향' 같은 전문 용어를 사용하십시오.

[Constraints]
- 직접적인 제품 홍보, 할인 안내, 저렴한 마케팅 문구를 절대 금지합니다.
- 광고 모델 같은 인위적인 미소, 스톡 이미지 느낌을 철저히 배제합니다.
- 뻔한 건강 정보는 버리고, 가장 앞서가는(Cutting-edge) 웰니스 인사이트만 선별하십시오.
- **[매우 중요] 논리적 연결성 (No Clickbait)**: 카카오톡 프리뷰(Teaser)에서 제기한 질문이나 후킹용 소재를, 웹 아티클(Insight) 본문 서두에서 반드시 가장 먼저, 상세히 논리적으로 설명하며 해소해야 합니다. 티저 내용과 본문 내용이 따로 노는 '동문서답' 형태를 철저히 금지합니다.
- **[매우 중요] 철저한 팩트 체크 및 근거 필수 (Anti-Hallucination)**: 
  아래 제공되는 <trend_data> 소스 기사에 **명시적으로, 실제로 존재하는 트렌드라고 적혀 있는 팩트(Fact)**만 사용하십시오. 
  예를 들어, 소스가 단순한 '도시 관광 추천'이나 '명상 일반론' 기사일 때, 이 둘을 자의적으로 결합하여 없는 트렌드를 지어내거나(Fabrication) 포장하는 행위를 절대 엄금합니다. 
  특정 도시가 원문에 명확하게 언급되지 않았다면, 그 트렌드를 임의의 도시와 억지로 엮어내지 마십시오. 세대, 성별 혹은 글로벌 차원의 넓은 관점에서 서술하십시오.
  반드시 소스 기사나 구글 검색에서 "이러한 현상이 실제 트렌드로 자리잡고 있다"는 구체적인 근거가 있을 때만 해당 내용을 작성하십시오.

반드시 아래의 정해진 JSON 형식(Key 이름 정확히 일치)으로만 결과를 반환해야 하며, 마크다운 코드 블록(```json ... ```) 없이 순수한 JSON 텍스트 상태로 출력하십시오.

[필수 JSON 반환 포맷]
{
  "kakao_teaser": "카톡 티저 텍스트...",
  "web_article": "웹 아티클 본문...",
  "editor_note": "내가 왜 이 소스를 골랐고, 구글 검색(Grounding)을 통해 어떤 팩트를 검증하여 이 논리로 글을 작성했는지 상세히 서술...",
  "reference_links": [
    {"url": "https://...", "comment": "코멘트..."}
  ],
  "visual_prompt": "화보 영문 프롬프트..."
}
"""


# ---------------------------------------------------------------------------
# 3. 부가 기능: 로컬 컨텍스트 읽기, 웹 검색, 노션 히스토리 조회
# ---------------------------------------------------------------------------
def read_local_context(file_path: str) -> str:
    """로컬 마크다운 파일(가이드라인, 샘플)을 읽어옵니다."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logging.error(f"로컬 파일 읽기 실패 ({file_path}): {e}")
        return ""


def scrape_article_text(url: str) -> str:
    """BeautifulSoup을 이용해 원문 URL에서 기사 본문 텍스트만 추출합니다."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.google.com/'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 불필요한 태그 제거 (광고, 스크립트, 네비게이션 등)
        for junk in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
            junk.decompose()
            
        # 본문 핵심이 보통 <article>이나 본문 단락 <p>에 있음
        paragraphs = soup.find_all('p')
        text = ' '.join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20])
        return text[:3000]  # 토큰 제한을 고려하여 기사당 최대 3000자로 제한
    except Exception as e:
        logging.warning(f"기사 스크래핑 실패 ({url}): {e}")
        return ""

def scrape_premium_rss_feeds(limit_per_feed: int = 2) -> tuple:
    """글로벌 최고급 웰니스 매거진의 RSS 피드를 파싱하고 본문을 통째로 긁어옵니다."""
    logging.info("프리미엄 매거진 RSS 스크래핑 시작...")
    
    # User-Agent 위장 (일부 사이트 봇 타겟팅 차단 우회)
    feedparser.USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.0.0 Safari/537.36"
    
    # 대표적인 글로벌 프리미엄 웰니스/라이프스타일 매거진 RSS 목록
    rss_urls = [
        "https://rss.nytimes.com/services/xml/rss/nyt/Well.xml",  # NYT Well
        "https://www.theguardian.com/lifeandstyle/health-and-wellbeing/rss", # Guardian Wellness
        "https://www.mindbodygreen.com/rss/feed.xml", # MindBodyGreen (권위있는 웰니스 미디어)
        "https://www.wellandgood.com/feed/", # Well+Good (트렌디 피트니스 & 뷰티)
        "https://www.vogue.com/feed/beauty/rss", # Vogue Beauty & Wellness
        "https://www.womenshealthmag.com/rss/all.xml", # Women's Health
        "https://www.menshealth.com/rss/all.xml" # Men's Health
    ]
    
    results_text = ""
    urls_list = []
    
    for feed_url in rss_urls:
        try:
            # RSS 직접 다운로드 방식으로 차단 우회
            headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            res = requests.get(feed_url, headers=headers, timeout=10)
            res.raise_for_status()
            
            feed = feedparser.parse(res.text)
            for entry in feed.entries[:limit_per_feed]:
                title = entry.title
                link = entry.link
                
                # 원문 스크래핑 시도
                article_body = scrape_article_text(link)
                
                if article_body:
                    results_text += f"---\n[Source: {feed_url}]\n- 제목: {title}\n- 링크: {link}\n- 본문 내용:\n{article_body}\n\n"
                    urls_list.append(link)
        except Exception as e:
            logging.error(f"RSS 파싱 오류 ({feed_url}): {e}")
            
    if not results_text:
        results_text = "RSS에서 기사를 수집하지 못했습니다."
        
    logging.info(f"프리미엄 스크래핑 완료. (총 {len(urls_list)}개 기사 본문 확보)")
    return results_text, "\n".join(urls_list)


def get_past_notion_topics(limit: int = 10) -> str:
    """노션 데이터베이스에서 최근 발행된 주제(Title) 목록을 가져옵니다."""
    logging.info("노션 과거 발행 이력 조회 시작...")
    if not notion_client or not NOTION_DATABASE_ID:
        return "노션 설정이 없어 과거 이력을 조회할 수 없습니다."
        
    topics = []
    try:
        # 최근 생성일 기준으로 정렬하여 가져오기
        response = notion_client.databases.query(
            **{
                "database_id": NOTION_DATABASE_ID,
                "page_size": limit,
                "sorts": [{"timestamp": "created_time", "direction": "descending"}]
            }
        )
        for page in response.get("results", []):
            # '주제' 속성이 Title 타입이라고 가정
            props = page.get("properties", {})
            topic_prop = props.get("주제", {})
            title_list = topic_prop.get("title", [])
            if title_list:
                topics.append(title_list[0].get("plain_text", ""))
                
        logging.info(f"과거 이력 {len(topics)}건 조회 완료.")
    except Exception as e:
        logging.error(f"노션 조회 중 오류 발생: {e}")
        
    if not topics:
        return "최근 발행된 주제가 없습니다."
    return ", ".join(topics)


# ---------------------------------------------------------------------------
# 4. 핵심 AI 생성 로직
# ---------------------------------------------------------------------------
def generate_editorial_content(trend_data: str, past_topics: str, brand_identity: str, sample_article: str) -> Dict[str, str]:
    """검색 데이터, 과거 이력, 로컬 컨텍스트를 종합하여 에디토리얼을 생성합니다."""
    logging.info("AI 에디터 콘텐츠 생성 시작 (Google Search Grounding 활성화)...")
    
    if not gemini_client:
        raise ValueError("Gemini API Key가 설정되지 않았습니다.")

    user_prompt = f"""
[Brand Identity & Constraints]
{brand_identity}

[Sample References]
{sample_article}

---
[Step 1. 영감의 원천 (Premium Sources)]
다음은 이번주 글로벌 최고급 웰니스 매거진에서 수집된 최신 기사 본문들입니다:
<trend_data>
{trend_data}
</trend_data>

[Step 2. 팩트체크 및 트렌드 결합 (Google Search Grounding)]
위 기사들 중 가장 영감이 되는 주제 하나를 고르십시오. 
그리고 **당신의 구글 검색(Search Grounding) 능력을 즉시 가동하여**, 해당 주제가 현재 글로벌 피트니스/웰니스 씬에서 실제로 어떻게 발현되고 있는지 구체적인 팩트(실제 명소, 스튜디오, 브랜드, 커뮤니티 현상 등)를 직접 검색하고 검증하십시오.
- 구글 검색을 통해 팩트에 기반한 구체적인 사례를 찾아 보완하십시오.
- [매우 중요] 만일 기사 원문이나 검색 결과가 특정 도시(예: 런던, 코펜하겐 등)에 국한된 내용이 아니라면, 억지로 특정 로컬 도시 이름을 언급하여 환각(Fabrication) 트렌드를 만들어내지 마십시오. 대신 '글로벌 하이엔드 웰니스 씬', '밀레니얼/Z세대 피트니스 문화' 등 넓고 지적인 관점으로 서술하십시오.
- 프리미엄 소스의 철학적 인사이트와 구글 검색으로 확인된 실제 사례를 매끄럽게 결합하여 최고의 웰니스 아티클을 작성하십시오.

[주의 사항]
최근에 이미 발행된 다음 주제들과는 **절대 겹치지 않는 새로운 앵글**로 작성해야 합니다.
<past_topics>
{past_topics}
</past_topics>

이 모든 정보와 검색결과를 바탕으로, 위시 리추얼 채널에 발행할 3가지 포맷(kakao_teaser, web_article, visual_prompt)과, 당신이 실제로 활용한 최고급 소스+구글검색 URL들을 reference_links 필드에 정리하여 생성하십시오.
"""

    response = gemini_client.models.generate_content(
        model="gemini-2.5-pro",
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[{"google_search": {}}],  # Google Search Grounding 기능 활성화
            temperature=0.7,
        )
    )
    
    # 순수 JSON 처리
    cleaned_res = response.text.strip()
    if cleaned_res.startswith("```json"):
        cleaned_res = cleaned_res[7:]
    if cleaned_res.startswith("```"):
        cleaned_res = cleaned_res[3:]
    if cleaned_res.endswith("```"):
        cleaned_res = cleaned_res[:-3]
    cleaned_res = cleaned_res.strip()
    
    try:
        result_dict = json.loads(cleaned_res)
    except json.JSONDecodeError:
        logging.error(f"JSON 파싱 실패: {response.text}")
        raise ValueError("Gemini API가 유효한 JSON을 반환하지 않았습니다.")
        
    logging.info("에디토리얼 콘텐츠 생성 완료.")
    return result_dict


def upload_to_notion(content_dict: Dict[str, Any], topic_title: str):
    """생성된 콘텐츠와 AI가 직접 필터링한 참고 링크를 노션 데이터베이스에 적재합니다."""
    logging.info("Notion 대시보드 적재 시작...")
    
    if not notion_client or not NOTION_DATABASE_ID:
        raise ValueError("Notion API Token 또는 Database ID가 설정되지 않았습니다.")

    # 발송 예정일 계산 (다음 주 화요일 오전 8시)
    now = datetime.now()
    days_ahead = 1 - now.weekday() # Tuesday is 1
    if days_ahead <= 0: # Target day already happened this week
        days_ahead += 7
    next_tuesday = now + timedelta(days=days_ahead)
    # 한국 시간 기준(KST, UTC+9)임을 명시하기 위해 +09:00 추가
    target_date_str = next_tuesday.replace(hour=8, minute=0, second=0, microsecond=0).isoformat() + "+09:00"
    
    # AI가 선별한 레퍼런스 링크를 노션 rich_text 링크 객체로 변환
    reference_links = content_dict.get("reference_links", [])
    rich_text_links = []
    
    if isinstance(reference_links, list):
        for ref in reference_links:
            url = ref.get("url", "").strip()
            comment = ref.get("comment", "").strip()
            if url:
                rich_text_links.append({
                    "type": "text",
                    "text": {
                        "content": f"[{comment}] {url}\n",
                        "link": {"url": url}
                    }
                })
    
    if not rich_text_links:
        rich_text_links = [{"text": {"content": "참고 링크 없음"}}]
    
    try:
        new_page = notion_client.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties={
                "주제": {
                    "title": [
                        {"text": {"content": topic_title}}
                    ]
                },
                "발송 예정일": {
                    "date": {
                        "start": target_date_str
                    }
                },
                "상태": {
                    "multi_select": [
                        {"name": "AI 작성 완료"}
                    ]
                },
                "카톡 초안": {
                    "rich_text": [
                        {"text": {"content": content_dict.get("kakao_teaser", "")[:2000]}}
                    ]
                },
                "웹 아티클": {
                    "rich_text": [
                        {"text": {"content": content_dict.get("web_article", "")[:2000]}}
                    ]
                },
                "참고 링크": {
                    "rich_text": rich_text_links[:10]  # 최대 10개까지만 안전하게 적재
                },
                "에디터 노트": {
                    "rich_text": [
                        {"text": {"content": content_dict.get("editor_note", "")[:2000]}}
                    ]
                },
                "화보 프롬프트": {
                    "rich_text": [
                        {"text": {"content": content_dict.get("visual_prompt", "")[:2000]}}
                    ]
                }
            }
        )
        logging.info(f"Notion 페이지 생성 성공! Page ID: {new_page['id']}")
    except Exception as e:
        logging.error(f"Notion 업로드 중 오류 발생: {e}")


# ---------------------------------------------------------------------------
# 메인 실행 블록
# ---------------------------------------------------------------------------
def main():
    try:
        # 1. 로컬 브랜드 가이드 및 샘플 읽기
        base_dir = os.path.dirname(os.path.abspath(__file__))
        brand_id_text = read_local_context(os.path.join(base_dir, "brand_identity.md"))
        sample_text = read_local_context(os.path.join(base_dir, "sample_article.md"))
        
        # 2. 글로벌 웰니스/라이프스타일 매거진 RSS 통째 본문 스크래핑
        scraped_trends, source_links = scrape_premium_rss_feeds(limit_per_feed=2)
        
        # 3. 과거 노션 이력 조회 (중복 방지 - 최근 50건까지 대폭 상향하여 철저히 검증)
        past_topics_text = get_past_notion_topics(limit=50)
        
        # 4. AI 에디터 콘텐츠 생성 (Search Grounding 적용)
        content = generate_editorial_content(
            trend_data=scraped_trends,
            past_topics=past_topics_text,
            brand_identity=brand_id_text,
            sample_article=sample_text
        )
        
        # 5. 노션 대시보드 적재
        # 제목 추출 (카톡 티저의 첫 문장 정도를 주제로 사용)
        topic_preview = content.get("kakao_teaser", "").split("\n")[0][:40] + "..."
        upload_to_notion(content, topic_preview)
        
    except Exception as e:
        logging.error(f"파이프라인 실행 중 치명적 오류: {e}")

if __name__ == "__main__":
    main()
