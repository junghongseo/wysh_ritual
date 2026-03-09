import os
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

class TestOutput(BaseModel):
    title: str = Field(description="Article title")
    body: str = Field(description="Article body based on real-time search")

# Use google search tool
try:
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents="Search Google for the trendy new wellness startup 'Viome' or 'Athletic Greens' and write a short summary.",
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=TestOutput,
            temperature=0.7,
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )
    )
    print(response.text)
except Exception as e:
    print("Error:", e)
