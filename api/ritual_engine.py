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
notion = Client(auth=NOTION_TOKEN) if NOTION_TOKEN else None


# ---------------------------------------------------------------------------
# 1. Pydantic 스키마 정의 (LLM 출력 강제화)
# ---------------------------------------------------------------------------
class ReferenceLink(BaseModel):
    url: str = Field(description="실제로 참고하거나 영감을 얻은 기사의 원문 URL")
    comment: str = Field(description="이 기사의 어떤 내용을 아티클 작성에 활용했는지 한 줄 요약")


class EditorialContent(BaseModel):
    core_topic: str = Field(description="이 아티클의 핵심 주제를 나타내는 간결하고 명확한 1~3단어 키워드 (예: '12-3-30 워크아웃', '슈퍼에이저')")
    hooking_title: str = Field(description="사람들을 후킹할 수 있는 매력적이고 짧은 제목 (예: '결정 피로 시대의 가장 우아한 해답')")
    kakao_teaser: str = Field(description="카카오톡 프리뷰용 텍스트. 독자의 호기심을 유발하며, 마지막 문장에는 **오늘 내 삶에 당장 적용해볼 수 있는 구체적이고 가벼운 실천 팁(Actionable Tip)** 한 줄을 반드시 포함할 것.")
    insta_carousel: str = Field(description="인스타그램 카드뉴스용 텍스트. 총 4~8장의 슬라이드로 구성하며, [Slide 1] [Slide 2] 처럼 명시할 것. **[매우 중요] 각 슬라이드의 제목(Headline)은 절대로 영어를 쓰지 말고, '내 몸의 데이터가 식단을 결정한다'와 같이 도발적이고 세련된 한글 잡지(Magazine) 카피 스타일로만 작성할 것.** 아티클의 깊이 있는 내용을 충분히 담을 수 있도록, 각 슬라이드는 타이틀 외에 본문 텍스트가 최대 100자 내외(띄어쓰기 제외)가 되도록 풍성하고 구체적으로 작성할 것. 너무 짧은 단답형 문장을 지양하고, 반드시 한국어로 작성하며, **마지막 슬라이드에는 독자가 스스로 일상에서 어떻게 이 리추얼을 시도할 수 있는지 구체적인 실천 가이드(How to apply)**를 제안할 것.")
    web_article: str = Field(description="본문 아티클 (마크다운 포맷). 글로벌 트렌드를 깊이 있게 분석한 후, 글의 후반부에 독자가 자신의 일상 속에서 이 리추얼을 직접 시도하고 적용해 볼 수 있는 '구체적인 일상 실천 가이드(Actionable Tips)' 챕터를 자연스럽게 포함하여 작성할 것.")
    editor_note: str = Field(description="AI 에디터의 기획 의도, 선택한 소스에 대한 팩트체크 및 작성 논리를 설명하는 노트")
    reference_links: List[ReferenceLink] = Field(description="실제로 아티클 작성에 활용된 참고 소스 링크 및 활용 코멘트 목록")
    visual_prompt: str = Field(description="미드저니 등 이미지 생성을 위한 영문 프롬프트 (영어). 2030 트렌드세터 여성들이 열광하는 감성적인 '핀터레스트(Pinterest-core)' 및 인스타그램 피드 스타일. 인위적인 화보가 아닌, 자연스럽고 코지한 무드의 일상 필름 사진 느낌(35mm film photography, subtle grain, warm natural sunlight, candid lifestyle snapshot, warm and muted aesthetic, highly aesthetic, effortless chic)을 강조할 것. 반드시 프롬프트 맨 마지막에 가로:세로 3과 2의 비율을 나타내는 '--ar 3:2' 파라미터를 포함할 것.")


class SelectedSafeArticle(BaseModel):
    title: str = Field(description="선택된 안전한 기사의 제목")
    source_url: str = Field(description="선택된 기사의 원문 URL")
    reason: str = Field(description="이 기사를 고른 이유 (과거 이력과 어떻게 완전히 다르고 혁신적인지 설명)")

class SelectedSafeArticles(BaseModel):
    articles: List[SelectedSafeArticle] = Field(description="가장 훌륭한 최우선 순위 기사 3개 (1순위, 2순위, 3순위 순서대로 배열)")

# ---------------------------------------------------------------------------
# 2. 페르소나 및 시스템 프롬프트 정의
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """
당신은 글로벌 웰니스 매거진(예: Monocle, Kinfolk)의 편집장(Editor-in-Chief)입니다.
'WYSH'라는 브랜드를 단순한 식품 브랜드를 넘어, 전 세계 웰니스 라이프스타일을 선도하는 지적인 미디어로 만드십시오.

[Persona & Tone]
- 당신의 핵심 독자는 트렌드에 극도로 민감하고 글로벌 웰니스 라이프스타일을 빠르게 흡수하고자 하는 '2030 여성'입니다.
- 이들은 거대 담론보다 뉴욕, LA, 도쿄, 서울, 파리, 런던 등 전 세계 최고 핫한 도시에서 막 터지기 시작한 Z세대의 마이크로 푸드 문화, 로컬 팝업 스토어, 혁신 스타트업의 사례를 가장 흥미로워합니다.
- 친근하고 부드러운 정보 전달: 주요 어미는 반드시 "~해요.", "~요."를 사용하십시오. 너무 딱딱하거나 학술적인 문체는 배제하고 세련된 매거진 톤을 유지하십시오.
- '다이어트' 대신 '신체 최적화/퍼포먼스 지향/자기 관리' 같은 전문 용어를 사용하되, 비유를 들어 아주 매끄럽게 설명하십시오.

[Web Article Formatting]
- 웹 아티클 작성 시 **반드시 소제목으로 문단을 구분**하십시오.
- 각 소제목 앞에는 내용에 가장 잘 어울리는 **이모지(Emoji)를 1개씩 추가**하십시오. (예: ⏰ 식사에도 '타이밍'이 존재해요)
- **[매우 중요] 백화점식 나열(Listicle) 절대 금지**: 여러 가지 각기 다른 트렌드(A, B, C)를 첫째, 둘째 식으로 나열하는 글쓰기를 절대 금지합니다. 오직 '하나의 핵심 현상/트렌드'만을 골라, 서론(현상 제기) - 본론(원인과 깊이 있는 분석, 브랜드 사례) - 결론(인사이트)으로 물 흐르듯 이어지는 **하나의 깊이 있는 에세이(Essay) 형태**로 작성하십시오.
- 너무 길어지지 않도록, **각 문단(하나의 소제목 아래 내용)은 200자 내외**로 아주 짧고 가독성 좋게 작성하십시오.

[Constraints]
- 직접적인 제품 홍보, 할인 안내, 저렴한 마케팅 문구를 절대 금지합니다.
- 광고 모델 같은 인위적인 미소, 스톡 이미지 느낌을 철저히 배제합니다.
- 뻔한 건강 정보는 버리고, 가장 앞서가는(Cutting-edge) 웰니스 인사이트만 선별하십시오.
- **[매우 중요] 단일 주제 집중 (Single Theme Focus)**: 제공된 3개의 트렌드 소스 기사 내용들을 기계적으로 조립하거나 병렬적으로 엮어내지 마십시오. 전혀 연관성 없는 트렌드들(예: 맞춤형 영양제와 얼리케어, 식물성 대체육 등)을 한 글에 구겨 넣으면 글의 일관성이 완전히 무너집니다. **소스 기사들 중에서 가장 매력적이고 강력한 단 1개의 현상/트렌드만 선택**하십시오. 나머지 소스는 과감히 버리고, 오직 선택한 1개의 소재에만 100% 집중하여 기둥이 되는 하나의 스토리를 만드십시오.
- **[매우 중요] 논리적 연결성 (No Clickbait)**: 카카오톡 프리뷰(Teaser)에서 제기한 단 하나의 질문이나 후킹용 소재를, 웹 아티클 본문 서두에서 반드시 가장 먼저, 상세히 논리적으로 설명하며 해소해야 합니다.
  - **[절대 금지 사항]**: 단, 웹 아티클 본문 안에 "카카오톡 티저에서 언급했듯이", "앞서 카톡에서"와 같이 **'카카오톡', '티저', '프리뷰'**라는 단어를 직접적으로 언급하는 메타 발언(Meta-reference)을 절대 금지합니다. (독자가 웹 검색을 통해 바로 아티클로 들어왔다고 가정하고 자연스럽고 완성도 있게 글을 시작하십시오.)
- **[출처 인용 (Source Citation)]**: 제공된 소스 기사 본문에 연구 기관, 논문, 대학교, 전문가 이름, 저널, 특정 통계 등의 출처가 언급되어 있다면, 당신이 작성하는 웹 아티클 본문에서도 그 출처를 매우 자연스럽고 지적이게 언급하여 아티클의 신뢰도와 권위를 높이십시오.
- **[매우 중요] 철저한 팩트 체크 및 근거 필수 (Anti-Hallucination)**: 
  아래 제공되는 <trend_data> 소스 기사에 **명시적으로, 실제로 존재하는 트렌드라고 적혀 있는 팩트(Fact)**만 사용하십시오. 
  예를 들어, 소스가 단순한 '도시 관광 추천'이나 '명상 일반론' 기사일 때, 이 둘을 자의적으로 결합하여 없는 트렌드를 지어내거나(Fabrication) 포장하는 행위를 절대 엄금합니다. 
  특정 도시가 원문에 명확하게 언급되지 않았다면, 그 트렌드를 임의의 도시와 억지로 엮어내지 마십시오. 세대, 성별 혹은 글로벌 차원의 넓은 관점에서 서술하십시오.
  반드시 소스 기사나 구글 검색에서 "이러한 현상이 실제 트렌드로 자리잡고 있다"는 구체적인 근거가 있을 때만 해당 내용을 작성하십시오.
- **[실천을 위한 팁 (Actionable Tips)]**: 단순히 지식과 트렌드를 전달하는 데 그치지 마십시오. 독자가 글을 다 읽은 내일 아침, 당장 자기 방에서 혹은 식탁에서 무엇을 어떻게 시도해 볼 수 있는지, 비용이 들지 않는 작고 구체적이며 현실적인(Actionable) 행동 지침을 포함하십시오.
- **[요즘 핫한 푸드 브랜드 및 제품 추천 (Brand Discovery)]**: **[매우 중요]** 트렌드 설명에만 그치지 말고, 해당 트렌드를 리드하고 있는 **실제 글로벌/로컬의 핫한 푸드 브랜드, 혁신적인 스타트업, 또는 구체적인 제품 사례**를 구글 검색을 통해 적극적으로 발굴하여 기사 본문에 자연스럽게 녹여내십시오. (예: 특정 슈퍼푸드를 활용해 돌풍을 일으킨 스낵 브랜드, 새로운 대체 당을 개발한 푸드테크 기업, Z세대가 열광하는 비건 음료 등)

- **[언어 설정]**: 프롬프트 시각화용 영문 텍스트(visual_prompt)를 제외한 모든 콘텐츠(kakao_teaser, insta_carousel, web_article, editor_note, core_topic, hooking_title 등)는 **반드시 유려하고 세련된 한국어로** 작성하십시오. 영어가 섞이더라도 메인 언어는 한국어여야 합니다.

반드시 아래의 정해진 JSON 형식(Key 이름 정확히 일치)으로만 결과를 반환해야 하며, 마크다운 코드 블록(```json ... ```) 없이 순수한 JSON 텍스트 상태로 출력하십시오.

[필수 JSON 반환 포맷]
{
  "core_topic": "핵심 주제 키워드 (예: '12-3-30 워크아웃')",
  "hooking_title": "사람들을 후킹할 수 있는 매력적인 짧은 제목...",
  "kakao_teaser": "카톡 티저 텍스트...",
  "insta_carousel": "[Slide 1] 첫 번째 슬라이드 내용... \n\n[Slide 2] 두 번째 슬라이드 내용...",
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
    """로컬 마크다운 파일(가이드라인, 샘플)을 읽어옵니다. 루트 디렉토리 기준"""
    # Vercel 환경에서는 api/ 하위가 아닌 루트 디렉토리(..)를 기준으로 파일을 찾아야 함
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        full_path = os.path.join(base_dir, file_path)
        with open(full_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logging.error(f"로컬 파일 읽기 실패 ({full_path}): {e}")
        return ""

def scrape_premium_rss_feeds(limit_per_feed: int = 4, exclude_urls: list = None, target_category: str = "") -> tuple:
    """글로벌 최고급 웰니스 매거진의 식음료/영양 관련 RSS 피드를 파싱하고 본문을 통째로 긁어옵니다."""
    logging.info("푸드/영양 특화 매거진 RSS 스크래핑 시작...")
    
    if exclude_urls is None:
        exclude_urls = []
        
    # User-Agent 위장 (일부 사이트 봇 타겟팅 차단 우회)
    feedparser.USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.0.0 Safari/537.36"
    
    # 식음료, 다이어트, 영양, 푸드 트렌드 전용 Bing 메인 뉴스 RSS 피드 (자바스크립트 우회 차단 없음)
    # GitHub Actions 환경이므로 피드 개수를 대폭 늘림 (다양한 검색어)
    food_focused_rss_urls = [
        "https://www.bing.com/news/search?q=superaliments+nutrition+tendance+marque&format=rss&mkt=fr-fr",
        "https://www.bing.com/news/search?q=%E3%82%B9%E3%83%BC%E3%83%91%E3%83%BC%E3%83%95%E3%83%BC%E3%83%89+%E3%83%80%E3%82%A4%E3%82%A8%E3%83%83%E3%83%88+%E9%A3%9F%E5%93%81+%E3%83%96%E3%83%A9%E3%83%B3%E3%83%89&format=rss&mkt=ja-jp",
        "https://www.bing.com/news/search?q=Ern%C3%A4hrung+Superfood+Vegan+Startup+Brand&format=rss&mkt=de-de",
        "https://www.bing.com/news/search?q=kost+n%C3%A4ring+superfood+varum%C3%A4rke+trend&format=rss&mkt=sv-se",
        "https://www.bing.com/news/search?q=%EC%8A%88%ED%8D%BC%ED%91%B8%EB%93%9C+%EC%8B%9D%EB%8B%A8+%EC%98%81%EC%96%91+%ED%91%B8%EB%93%9C+%EB%B8%8C%EB%9E%9C%EB%93%9C+%ED%8A%B8%EB%88%8C%EB%93%9C&format=rss&mkt=ko-kr",
        "https://www.bing.com/news/search?q=trendy+food+brands+wellness+startup+nutrition&format=rss&mkt=en-us",
        "https://www.bing.com/news/search?q=gen+z+food+culture+trend+wellness&format=rss&mkt=en-us",
        "https://www.bing.com/news/search?q=new+york+wellness+food+startup+trend&format=rss&mkt=en-us",
        "https://www.bing.com/news/search?q=los+angeles+food+startup+wellness+trend&format=rss&mkt=en-us",
        "https://www.bing.com/news/search?q=tokyo+cafe+food+trend+gen+z&format=rss&mkt=en-us",
        "https://www.bing.com/news/search?q=seoul+fnb+popup+food+tech+brand&format=rss&mkt=ko-kr",
        "https://www.bing.com/news/search?q=london+vegan+wellness+food+startup&format=rss&mkt=en-gb",
        "https://www.bing.com/news/search?q=paris+superfood+wellness+diet&format=rss&mkt=fr-fr",
        "https://www.bing.com/news/search?q=berlin+plant+based+food+trend&format=rss&mkt=de-de",
        "https://www.bing.com/news/search?q=singapore+food+tech+startup+nutrition&format=rss&mkt=en-sg"
    ]
    
    import random
    
    rss_urls = list(food_focused_rss_urls)
    random.shuffle(rss_urls)
    
    # GitHub Action은 6시간 리밋이므로 기존 5개에서 10~15개 피드 전부 안전하게 검사하여 소스 풀을 대규모 확장
    rss_urls = rss_urls[:12]
    
    results_text = ""
    urls_list = []
    
    for feed_url in rss_urls:
        try:
            # RSS 직접 다운로드 방식으로 차단 우회
            headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            res = requests.get(feed_url, headers=headers, timeout=10)
            res.raise_for_status()
            
            feed = feedparser.parse(res.text)
            added_count = 0
            for entry in feed.entries:
                if added_count >= limit_per_feed:
                    break
                    
                title = entry.title
                link = entry.link
                
                # Bing 뉴스의 경우 내부에 실제 url 파라미터가 숨어있음
                import urllib.parse
                parsed_url = urllib.parse.urlparse(link)
                query_params = urllib.parse.parse_qs(parsed_url.query)
                actual_url = query_params.get('url', [link])[0]
                
                # 쿼리스트링 및 해시 태그 전부 제거하여 베이스 URL 추출
                base_link = actual_url.split("?")[0].split("#")[0].strip('/')
                
                # 이미 사용된 과거 URL 인 경우 1차 텍스트 필터 스킵
                if any(base_link in ex_url or ex_url in base_link for ex_url in exclude_urls if ex_url):
                    continue
                
                # 본문 스크래핑 생략! 제목과 링크만 추가하여 후보군 대량 확보
                results_text += f"- [{feed_url}] 제목: {title} | 링크: {link}\n"
                urls_list.append(link)
                added_count += 1
        except Exception as e:
            logging.error(f"RSS 파싱 오류 ({feed_url}): {e}")
            
    if not results_text:
        results_text = "RSS에서 기사 제목을 수집하지 못했습니다."
        
    logging.info(f"빠른 스크래핑 완료. (총 {len(urls_list)}개 기사 후보 제목/URL 확보)")
    return results_text, "\n".join(urls_list)


def get_past_notion_data(limit: int = 100) -> tuple[str, list]:
    """노션 데이터베이스에서 최근 발행된 주제(Title) 내의 [핵심 주제]와 참고 링크 목록을 가져옵니다."""
    logging.info("노션 과거 발행 이력 조회 시작...")
    if not notion or not NOTION_DATABASE_ID:
        return "노션 설정이 없어 과거 이력을 조회할 수 없습니다.", []
        
    banned_topics = []
    past_urls = []
    import re
    try:
        # 최근 생성일 기준으로 정렬하여 가져오기
        response = notion.databases.query(
            **{
                "database_id": NOTION_DATABASE_ID,
                "page_size": limit,
                "sorts": [{"timestamp": "created_time", "direction": "descending"}]
            }
        )
        for page in response.get("results", []):
            props = page.get("properties", {})
            
            # 주제 추출: '[차이나맥싱] 어쩌구저쩌구' 에서 '차이나맥싱'만 추출
            topic_prop = props.get("주제", {})
            title_list = topic_prop.get("title", [])
            if title_list:
                full_title = title_list[0].get("plain_text", "")
                match = re.search(r'\[([^\]]+)\]', full_title)
                if match:
                    banned_topics.append(match.group(1).strip())
                else:
                    banned_topics.append(full_title)
                
            # 참고 링크 추출
            links_prop = props.get("참고 링크", {})
            rich_texts = links_prop.get("rich_text", [])
            for rt in rich_texts:
                if rt.get("href"):
                    past_urls.append(rt["href"].split("?")[0].split("#")[0].strip('/'))
                elif rt.get("text", {}).get("link") and rt["text"]["link"].get("url"):
                    past_urls.append(rt["text"]["link"]["url"].split("?")[0].split("#")[0].strip('/'))
                
        logging.info(f"과거 이력(금지어) {len(banned_topics)}건 및 URL {len(past_urls)}건 조회 완료.")
    except Exception as e:
        logging.error(f"노션 조회 중 오류 발생: {e}")
        
    banned_topics_text = ", ".join(set(banned_topics)) if banned_topics else "최근 발행된 주제가 없습니다."
    return banned_topics_text, past_urls

def acquire_lock() -> tuple[str, str]:
    """
    Notion 문서 기반 분산 락을 획득합니다.
    [SYSTEM] Generating... 이라는 제목의 페이지가 존재하면 다른 프로세스가 작업 중인 것으로 판단합니다.
    (page_id, error_string) 형태의 튜플을 반환합니다.
    """
    logging.info("동시성 제어: Notion Lock 확인 중...")
    if not notion or not NOTION_DATABASE_ID:
        return "", "NOTION_CLIENT_NOT_INITIALIZED"
        
    try:
        from datetime import timezone
        # 최근 2분 이내에 생성된 락이 있는지 확인 (서버리스 타임아웃 60초 대비 빠른 락해제)
        # Vercel 환경이 UTC이므로 명시적으로 KST를 구해서 계산해야 함.
        kst = timezone(timedelta(hours=9))
        now_kst = datetime.now(kst)
        ten_mins_ago = (now_kst - timedelta(minutes=2)).isoformat()
        
        response = notion.databases.query(
            **{
                "database_id": NOTION_DATABASE_ID,
                "filter": {
                    "and": [
                        {
                            "property": "주제",
                            "title": {
                                "equals": "[SYSTEM] Generating..."
                            }
                        },
                        {
                            "timestamp": "created_time",
                            "created_time": {
                                "on_or_after": ten_mins_ago
                            }
                        }
                    ]
                }
            }
        )
        
        if response.get("results"):
            logging.warning("접근 거부: 이미 다른 기사 생성 프로세스가 실행 중입니다.")
            return "", "LOCK_EXISTS"
            
        # 락 획득 성공 시 락 페이지 생성
        new_page = notion.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties={
                "주제": {
                    "title": [
                        {"text": {"content": "[SYSTEM] Generating..."}}
                    ]
                },
                "상태": {
                    "multi_select": [
                        {"name": "AI 작성 완료"}
                    ]
                }
            }
        )
        logging.info(f"Notion Lock 획득 성공 (Page ID: {new_page['id']})")
        return new_page['id'], ""
        
    except Exception as e:
        logging.error(f"Notion Lock 확인 중 오류 발생: {e}")
        return "", str(e)

def release_lock(page_id: str):
    """지정된 page_id의 Notion Lock 문서를 영구 삭제(Archive)하여 락을 해제합니다."""
    if not page_id or not notion:
        return
        
    try:
        notion.pages.update(
            page_id=page_id,
            archived=True
        )
        logging.info("Notion Lock 해제 완료")
    except Exception as e:
        logging.error(f"Notion Lock 해제 실패: {e}")


# ---------------------------------------------------------------------------
# 4. 핵심 AI 생성 로직
# ---------------------------------------------------------------------------
def select_safe_article_with_ai(scraped_titles: str, past_topics: str, target_category: str) -> dict:
    """1단계 프리필터링: 대량의 기사 '제목' 후보 중 가장 타겟에 부합하고 과거 주제와 겹치지 않는 Top 3 URL을 선정합니다."""
    logging.info(f"AI 데스크팅: 타겟 카테고리 [{target_category}]에 맞는 안전한 새 기사 판별 중 (Step 1)...")
    
    if not gemini_client:
        return {}
        
    prompt = f"""
당신은 엄격하고 날카로운 데스크 에디터입니다.
이번 발행 호의 최우선 타겟 카테고리는 **[{target_category}]** 입니다.

아래 <new_articles>는 오늘 새로 수집된 글로벌 소스들입니다.
이 중에서 반드시 **[{target_category}]** 카테고리에 완벽히 부합하면서도, 
<past_topics>에 리스트업된 과거 식상한 주제들과는 '완전히 결이 다른', 가장 파격적이고 신선한 영감이 되는 기사 제목을 딱 3개(1순위, 2순위, 3순위) 골라주세요.

특히 거창한 이론이나 일반적인 건강 지식보다, **뉴욕, LA, 런던, 서울, 도쿄 등 글로벌 핫플레이스에서 막 떠오르는 Z세대의 식음료(F&B) 마이크로 트렌드, 스타트업이나 힙한 브랜드의 런칭 소식**을 다루고 있다면 무조건 1순위로 채택하십시오.

**[극도로 중요한 룰: 과거 반복 금지(Duplicate Penalty)]**
<past_topics>에 명시된 키워드(예: '비건', '식물성', 'CGM', '혈당', '모치무기', '맞춤형 영양제' 등)와 주제적으로 단 1%라도 겹치는 기사는 무조건 탈락시키십시오.
예를 들어 과거 이력에 '혈당'이 있다면, 오늘 수집된 40개의 기사 중에 '당뇨', '혈당 스파이크', '저당' 관련 뉴스는 아무리 매력적이어도 모두 제외해야 합니다.

만역 수집된 <new_articles_titles> 중 거의 대부분이 쓰레기거나 겹치는 것 같이 느껴지더라도, 그중 가장 '과거와 안 겹치는' 독특한 틈새(Niche) 브랜드나 엉뚱한 웰니스 식재료(예약하기 힘든 파인다이닝의 푸드 트렌드, 우주비행사 식단에서 온 트렌드 등)를 찾아내 3개를 반드시 뽑아내야 합니다.

<past_topics>
{past_topics}
</past_topics>

<new_articles_titles>
{scraped_titles}
</new_articles_titles>

선택한 3개의 기사 배열에 대해 각각 원문 제목(title), 원문 URL(source_url), 그리고 완벽하게 과거 중복을 피하고 새로운 트렌드를 제시했다는 구체적인 선택 이유(reason)를 JSON으로 설명해주세요.
"""
    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=SelectedSafeArticles,
                temperature=0.1
            )
        )
        res_dict = json.loads(response.text)
        articles = res_dict.get('articles', [])
        if articles:
            logging.info(f"AI 데스크팅 완료. Top 3 후보 확보! (1순위: [{articles[0].get('title')}])")
        return res_dict
    except Exception as e:
        logging.error(f"AI 데스크팅 실패: {e}")
        return {}

def search_and_draft_article(selected_title: str, selected_url: str, target_category: str) -> str:
    """구글 검색 그라운딩을 활용하여 최신 트렌드를 조사하고 초안을 작성합니다."""
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    
    prompt = f"""당신은 트렌디한 2030 여성을 타겟으로 하는 프리미엄 라이프스타일 매거진 에디터입니다.
    아래 선정된 웰니스/푸드 트렌드 기사에 대해 [Google Search] 툴을 적극 활용하여 최신 정보와 영감을 자유롭게 조사하십시오.
    
    [선별된 베이스 기사]
    제목: {selected_title}
    출처 URL: {selected_url}
    초점 카테고리: {target_category}
    
    [지시사항]
    1. 검색 엔진을 통해 해당 기사 혹은 연관된 최신 트렌드를 매우 방대하게 리서치하세요.
    2. 가장 중요하게 봐야 할 것은 다음과 같습니다: 뉴욕, LA, 런던, 파리, 싱가포르, 도쿄, 서울 등의 최전선(Cultural Hubs)에서 이 현상이 어떻게 소비되고 있는지 구체적인 사례(Z세대 문화, 실제 존재하는 스타트업/브랜드 제품, 힙스터들의 반응)를 반드시 최소 1~2개 이상 구글링으로 발굴하십시오.
    3. 수집된 정보를 바탕으로 매거진 아티클 "초안(Draft)"을 작성하세요. 추상적인 담론을 넘어 '어떤 글로벌 도시에서 어떤 브랜드가 이런 혁신을 이끌고 있는지' 눈에 그려지듯 상세하게 서술해야 2030 여성들의 흥미를 유발할 수 있습니다.
    4. 초안의 마지막 부분에는 2030 여성 독자가 이 힙한 트렌드를 자신의 일상으로 작게나마 가져와볼 수 있도록 하는 실용적인 가이드(Actionable Tips)를 포함하십시오.
    5. 당신이 구글링으로 찾아낸 실제 출처 링크(URL)나 브랜드 홈페이지들을 이 초안 마지막에 참고자료 목록으로 꼭 적어두세요.
    """
    
    # Gemini 2.5 flash 호출 (구글 검색 Grounding 활성화)
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.7,
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )
    )
    return response.text

def format_editorial_content(draft_text: str, source_url: str, brand_identity: str, sample_article: str) -> Dict[str, Any]:
    """초안을 완벽한 JSON 형식으로 포맷팅합니다 (Search Grounding 비활성화, Structured Output 활성화)"""
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    
    prompt = f"""당신은 Wysh 매거진의 편집장입니다.
    아래 제공된 [취재 초안]을 완벽하게 다듬어서 명시된 JSON(ResultSchema) 구조에 맞춰 출력하십시오.
    
    [필수 규칙, 페르소나, 그리고 JSON 구조]
    {SYSTEM_PROMPT}
    
    [AI 에디터의 취재 초안 (Google Search 기반 요약본)]
    {draft_text}
    
    [원문/출처 URL (Reference_links에 반영할 것)]
    {source_url}
    
    [브랜드 아이덴티티]
    {brand_identity}
    
    [참고용 아티클 샘플 (어조, 구성, 스타일 완벽 준수)]
    {sample_article}
    """
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=EditorialContent,
            temperature=0.7
        )
    )
    
    try:
        return json.loads(response.text)
    except json.JSONDecodeError as e:
        logging.error(f"JSON 파싱 실패: {e}")
        logging.error(f"Raw response: {response.text}")
        raise ValueError("AI가 반환한 결과물이 올바른 JSON 형식이 아닙니다.")

def generate_editorial_content(trend_data: str, brand_identity: str, sample_article: str, target_category: str) -> Dict[str, str]:
    """검색 데이터, 로컬 컨텍스트를 종합하여 에디토리얼을 생성합니다. (과거 금지어 프롬프트 제거)"""
    logging.info("AI 에디터 콘텐츠 생성 시작 (Step 2 - Google Search Grounding)...")
    
    if not gemini_client:
        raise ValueError("Gemini API Key가 설정되지 않았습니다.")

    user_prompt = f"""
[Brand Identity & Constraints]
{brand_identity}

이번 위시 리추얼의 핵심 타겟 영감 카테고리는 **[{target_category}]** 입니다.
이 렌즈를 통해 아래 기사들을 분석하고 서술하십시오.

[Sample References]
{sample_article}

---
[Step 1. 영감의 원천 (Premium Global Sources)]
다음은 북미, 유럽(프랑스, 스웨덴, 독일), 아시아(일본, 한국) 등 글로벌 각국에서 현지 언어로 수집된 최신 기사 본문입니다.
(프랑스어, 일본어 등의 언어는 영어/한국어로 자동 해석하여 이해하십시오)

<trend_data>
{trend_data}
</trend_data>

[Step 2. 팩트체크 및 트렌드 결합 (Google Search Grounding)]
위 세계 다국적 기사에서 영감을 얻어, **당신의 구글 검색(Search Grounding) 능력을 즉시 가동하여**, 해당 주제가 현재 글로벌 피트니스/웰니스 씬에서 실제로 어떻게 발현되고 있는지 구체적인 팩트(실제 명소, 스튜디오, 현지 커뮤니티 현상 등)를 직접 검색하고 검증하십시오.
- **[핵심 미션: 핫한 푸드 브랜드 소개]**: 트렌드 설명과 함께, **이 트렌드를 주도하거나 관련하여 현재 가장 핫한 글로벌/로컬 푸드 브랜드, 혁신적인 식음료 제품, 미식 스타트업의 실제 사례를 구글 검색을 통해 적극적으로 발굴하고 소개하십시오.** 이 브랜드가 어떤 철학과 제품으로 트렌드를 이끌고 있는지 구체적으로 언급하여 트렌드의 실체감을 높이십시오.
- 구글 검색을 통해 각 국가의 문화적 특수성이 반영된 통찰력 있는 사례를 찾아 보완하십시오. (예: 일본의 온천/수면 문화, 프랑스의 미식 웰니스, 스웨덴의 자연 친화적 리추얼 등 파생 검색)
- [매우 중요] 만일 기사 원문이나 검색 결과가 특정 도시(예: 런던, 코펜하겐 등)에 국한된 내용이 아니라면, 억지로 특정 로컬 도시 이름을 언급하여 환각(Fabrication) 트렌드를 만들어내지 마십시오. 대신 '글로벌 하이엔드 웰니스 씬', '밀레니얼/Z세대 피트니스 문화' 등 넓고 지적인 관점으로 서술하십시오.
- 프리미엄 소스의 철학적 인사이트와 구글 검색으로 확인된 실제 푸드 브랜드/제품 사례를 매끄럽게 결합하여 최고의 미식/웰니스 아티클을 작성하십시오.

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


def validate_link(url: str) -> bool:
    """URL에 실제 접속이 가능한지(404 Not Found 등이 아닌지) 검증합니다."""
    try:
        # User-Agent 설정 후 가벼운 HEAD 요청 (불가능할 경우 GET)
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        # 일단 timeout 3초를 주고 빠르게 확인
        res = requests.head(url, headers=headers, timeout=5, allow_redirects=True)
        if res.status_code >= 400:
            # HEAD가 막혀있는 사이트들을 대비해 GET으로 재시도
            res_get = requests.get(url, headers=headers, timeout=5)
            if res_get.status_code >= 400:
                logging.warning(f"접근 불가 링크 필터링됨 (상태코드 {res_get.status_code}): {url}")
                return False
        return True
    except Exception as e:
        logging.warning(f"접근 불가 링크 필터링됨 (에러): {url} -> {e}")
        return False

def upload_to_notion(content_dict: Dict[str, Any], topic_title: str):
    """생성된 콘텐츠와 AI가 직접 필터링한 참고 링크를 노션 데이터베이스에 적재합니다."""
    logging.info("Notion 대시보드 적재 시작...")
    
    if not notion or not NOTION_DATABASE_ID:
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
                if not validate_link(url):
                    continue  # 죽은 링크는 추가하지 않음
                rich_text_links.append({
                    "type": "text",
                    "text": {
                        "content": f"[{comment}] {url}\n",
                        "link": {"url": url}
                    }
                })
    
    if not rich_text_links:
        rich_text_links = [{"text": {"content": "참고 링크 없음"}}]
    
    # Notion은 rich_text.content 하나당 2000자를 초과할 수 없으므로 텍스트를 청크 분할하는 헬퍼 함수
    def chunk_text(text: str, chunk_size: int = 1900) -> list:
        if not text:
            return [{"text": {"content": ""}}]
        return [{"text": {"content": text[i:i+chunk_size]}} for i in range(0, len(text), chunk_size)]
        
    try:
        new_page = notion.pages.create(
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
                    "rich_text": chunk_text(content_dict.get("kakao_teaser", ""))
                },
                "인스타 카드뉴스": {
                    "rich_text": chunk_text(content_dict.get("insta_carousel", ""))
                },
                "웹 아티클": {
                    "rich_text": chunk_text(content_dict.get("web_article", ""))
                },
                "참고 링크": {
                    "rich_text": rich_text_links[:10]  # 최대 10개까지만 안전하게 적재
                },
                "에디터 노트": {
                    "rich_text": chunk_text(content_dict.get("editor_note", ""))
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
# 메인 실행 블록 (API 호환 구조로 변경)
# ---------------------------------------------------------------------------
def run_engine() -> Dict[str, Any]:
    """서버리스 API 호출을 위한 진입점입니다. 성공/실패 여부를 반환합니다."""
    lock_id = ""
    try:
        # 0. 동시성 제어 락 획득
        lock_id, lock_err = acquire_lock()
        if not lock_id:
            if lock_err == "LOCK_EXISTS":
                return {"status": "error", "message": "현재 기사를 생성 중입니다. 잠시 후 1~2분 뒤에 다시 시도해주세요."}
            else:
                return {"status": "error", "message": f"Lock Error: {lock_err}"}
            
        # 1. 로컬 브랜드 가이드 및 샘플 읽기 (api 폴더 밖의 루트 파일)
        brand_id_text = read_local_context("brand_identity.md")
        sample_text = read_local_context("sample_article.md")
        
        # 2. 과거 노션 이력 조회 (중복 방지 - 최근 100건까지 대폭 상향하여 철저히 검증)
        past_topics_text, past_urls = get_past_notion_data(limit=100)
        
        # 2.5 타겟 카테고리 고정 (당분간 미식/식음료/다이어트/영양 분야로 범위 한정 + 핫한 푸드 브랜드 소개)
        target_category = "미식, 다이어트, 영양, 건강식, 식음료 트렌드, 요즘 글로벌에서 핫한 혁신적인 푸드 브랜드 및 제품"
        logging.info(f"🎯 [타겟 고정] 이번 호 카테고리: {target_category}")

        # 3. 글로벌 웰니스/라이프스타일 매거진 RSS에서 '기사 제목/URL 후보군' 수집 (Vercel 타임아웃 방지를 위해 피드당 2개로 축소)
        scraped_titles, source_links = scrape_premium_rss_feeds(limit_per_feed=2, exclude_urls=past_urls, target_category=target_category)
        
        # 4. 1단계: AI 데스크팅 (30+ 후보 중 타겟 카테고리에 맞는 가장 안전한 Top 3 기사 URL 배열 선별)
        selected_articles_dict = select_safe_article_with_ai(scraped_titles, past_topics_text, target_category)
        candidates = selected_articles_dict.get("articles", [])
        
        if not candidates or len(candidates) == 0:
            raise ValueError("AI 데스크팅 단계에서 단 1개의 유효한 기사 후보도 선정하지 못했습니다. (프롬프트 과도한 제어로 인한 빈 배열 반환)")
            
        logging.info(f"선별된 {len(candidates)}개의 기사 후보를 바탕으로 안전망(Fallback) 리서치 루프 시작...")
        
        # 5. Fallback Search Grounding 루프
        draft_text = ""
        final_url = ""
        for index, article in enumerate(candidates):
            try:
                article_title = article.get("title", "")
                article_url = article.get("source_url", "")
                logging.info(f"[{index+1}순위] 기사 구글 리서치 시도 중: {article_title}")
                draft_text = search_and_draft_article(article_title, article_url, target_category)
                if draft_text and len(draft_text) > 200:
                    final_url = article_url
                    logging.info("=> 구글 리서치 및 초안 작성 성공!")
                    break
                else:
                    logging.warning(f"=> 반환된 초안 길이 부족. 다음 순위로 이동합니다.")
            except Exception as e:
                logging.warning(f"=> 리서치 실패 ({article.get('title')}): {str(e)}\n=> 즉시 다음 순위 후보 기사를 시도합니다.")
        
        if not draft_text or len(draft_text) < 200:
            raise ValueError(f"모든 기사 후보({len(candidates)}개)의 리서치 및 초안 획득에 실패했습니다.")
            
        # 6. JSON 데스크팅 (Formatting)
        logging.info("최종 JSON 포맷팅 작업 시작...")
        content = format_editorial_content(
            draft_text=draft_text, 
            source_url=final_url,
            brand_identity=brand_id_text, 
            sample_article=sample_text
        )
        
        core_topic = content.get("core_topic", "새로운 웰니스 트렌드")
        hooking_title = content.get("hooking_title", "이번 주 위시 리추얼")
        topic_preview = f"[{core_topic}] {hooking_title}"
        
        upload_to_notion(content, topic_preview)
        
        return {"status": "success", "message": f"노션 적재 성공: {topic_preview}"}
        
    except Exception as e:
        error_msg = str(e)
        logging.error(f"파이프라인 실행 중 오류: {error_msg}")
        return {"status": "error", "message": error_msg}
    finally:
        # 락 반납
        if lock_id:
            release_lock(lock_id)
import sys

if __name__ == "__main__":
    # 로컬 터미널 및 GitHub Actions 실행용
    res = run_engine()
    print(res)
    if isinstance(res, dict) and res.get("status") == "error":
        sys.exit(1)
