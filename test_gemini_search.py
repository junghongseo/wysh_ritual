import os
import google.generativeai as genai
from pydantic import BaseModel, Field
import json
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

class TestOutput(BaseModel):
    title: str = Field(description="Article title")
    body: str = Field(description="Article body based on real-time search")

# Use google search tool
model = genai.GenerativeModel('gemini-2.5-flash', tools='google_search')

try:
    response = model.generate_content(
        "Search Google for the trendy new wellness startup 'Viome' or 'Athletic Greens' and write a short summary.",
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=TestOutput,
            temperature=0.7
        )
    )
    print(response.text)
except Exception as e:
    print("Error:", e)
