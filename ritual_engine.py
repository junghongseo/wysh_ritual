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
from duckduckgo_search import DDGS

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
class EditorialContent(BaseModel):
    kakao_teaser: str = Field(description="카카오톡 프리뷰, 350자 이내, 잡지 커버의 메인 헤드라인처럼 감각적이고 호기심을 유발하는 문구.")
    web_article: str = Field(description="웹사이트 저널, 전문 에디터가 집필한 깊이 있는 아티클. 마크다운 형식.")
    visual_prompt: str = Field(description="화보 이미지 프롬프트 (영어), 하이엔드 매거진 스타일(35mm 렌즈, 자연광, 미니멀리즘 등).")


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

입력되는 최신 웰니스 트렌드를 바탕으로, 독자의 삶의 감각을 깨울 수 있는 콘텐츠(JSON)를 생성하십시오.
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


def search_wellness_trends(query: str = "뉴욕 런던 글로벌 웰니스 라이프스타일 트렌드", max_results: int = 3) -> str:
    """DuckDuckGo를 활용해 최신 웰니스 트렌드를 검색하고 텍스트로 반환합니다."""
    logging.info(f"웹 검색 시작: '{query}'")
    results_text = ""
    try:
        with DDGS() as ddgs:
            results = ddgs.text(query, max_results=max_results, region='kr-kr')
            for r in results:
                results_text += f"- 제목: {r.get('title')}\n  내용: {r.get('body')}\n\n"
        logging.info("웹 검색 완료.")
    except Exception as e:
        logging.error(f"웹 검색 중 오류 발생: {e}")
        results_text = "검색 데이터를 가져오지 못했습니다."
    return results_text


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
    logging.info("AI 에디터 콘텐츠 생성 시작...")
    
    if not gemini_client:
        raise ValueError("Gemini API Key가 설정되지 않았습니다.")

    user_prompt = f"""
[Brand Identity & Constraints]
{brand_identity}

[Sample References]
{sample_article}

---
다음은 이번주 글로벌 도시에서 수집된 최신 웰니스 트렌드 정보입니다:
<trend_data>
{trend_data}
</trend_data>

[주의 사항]
최근에 이미 발행된 다음 주제들과는 **절대 겹치지 않는 새로운 앵글**로 작성해야 합니다.
<past_topics>
{past_topics}
</past_topics>

이 정보들을 바탕으로, 위시 리추얼 채널에 발행할 3가지 포맷(kakao_teaser, web_article, visual_prompt)을 생성해주십시오.
"""

    response = gemini_client.models.generate_content(
        model="gemini-2.5-pro",
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=EditorialContent,
            temperature=0.7,
        )
    )
    
    # 텍스트 응답을 JSON으로 파싱 (schema를 강제했으므로 정상적인 JSON 문자열이 반환됨)
    try:
        result_dict = json.loads(response.text)
    except json.JSONDecodeError:
        logging.error(f"JSON 파싱 실패: {response.text}")
        raise ValueError("Gemini API가 유효한 JSON을 반환하지 않았습니다.")
        
    logging.info("에디토리얼 콘텐츠 생성 완료.")
    return result_dict


def upload_to_notion(content_dict: Dict[str, str], topic_title: str):
    """생성된 콘텐츠를 노션 데이터베이스에 적재합니다."""
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
        
        # 2. 웹 트렌드 자율 검색 (매번 다채로운 도시와 라이프스타일 주제를 조합)
        cities = ["런던", "토쿄", "파리", "베를린", "스톡홀름", "시드니", "뉴욕", "코펜하겐", "바르셀로나"]
        categories = [
            "하이엔드 식음료(F&B)", 
            "프리미엄 피트니스 및 러닝 크루", 
            "마인드풀니스 및 멘탈케어", 
            "기능성 패션 및 뷰티 트렌드", 
            "하이엔드 취미 및 미디어 소비",
            "초개인화 웰니스 및 수면관리"
        ]
        
        selected_city = random.choice(cities)
        selected_category = random.choice(categories)
        trend_keywords = f"최신 {selected_city} 웰니스 {selected_category} 라이프스타일 트렌드"
        
        logging.info(f"이번 주 큐레이션 타겟: 도시='{selected_city}', 카테고리='{selected_category}'")
        
        scraped_trends = search_wellness_trends(query=trend_keywords, max_results=5)
        
        # 3. 과거 노션 이력 조회 (중복 방지)
        past_topics_text = get_past_notion_topics(limit=5)
        
        # 4. AI 에디터 콘텐츠 생성
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
