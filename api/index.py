from http.server import BaseHTTPRequestHandler
import json
import logging
from api.ritual_engine import run_engine

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        """웹 프론트엔드에서 버튼 클릭 시 POST 요청을 받아 처리합니다."""
        logging.info("Vercel Serverless Function: POST /api/run 호출됨")
        
        # 1. CORS 헤더 설정 (프론트 통신용)
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        
        # 2. 메인 AI 엔진 구동 (시간이 오래 걸림 30~50s)
        try:
            result = run_engine()
            response_data = json.dumps(result)
        except Exception as e:
            response_data = json.dumps({"status": "error", "message": str(e)})
            
        # 3. 결과 반환
        self.wfile.write(response_data.encode('utf-8'))

    def do_OPTIONS(self):
        """CORS Preflight 요청 처리"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
