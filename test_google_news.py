import requests
from bs4 import BeautifulSoup

url = "https://news.google.com/rss/articles/CBMiakFVX3lxTE9mUld2Y3h0RjFBUDdrd3JPcldGYVNrYktOYXV5cTB1TmtOMEtOM1lQSXJRbkh5RzJMVFBDOFdWR3IzYTJUbG1sWTZ5UlFOTHFiT05xb1IzaTVuZ2UxT0dQZ3daSHZpNlJ5WWc?oc=5"
headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
try:
    res = requests.get(url, headers=headers, timeout=10)
    print("Status:", res.status_code)
    print("Final URL:", res.url)
    
    soup = BeautifulSoup(res.text, 'html.parser')
    paragraphs = soup.find_all('p')
    text = ' '.join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20])
    print("Extracted Text Length:", len(text))
    print("HTML excerpt:", res.text[:500])
except Exception as e:
    print("Error:", e)
