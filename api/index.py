from http.server import BaseHTTPRequestHandler
import json
import logging
import os
import urllib.request
import urllib.error
import urllib.parse

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        """웹 프론트엔드에서 버튼 클릭 시 POST 요청을 받아 GitHub Actions를 트리거합니다."""
        logging.info("Vercel Serverless Function: POST /api/run 호출됨 -> GitHub Action 트리거 시작")
        
        # 1. CORS 헤더 설정
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        
        # 2. GitHub Actions 트리거 정보
        # Vercel 환경변수에 GITHUB_TOKEN(PAT)과 깃허브 레포 주소가 설정되어 있어야 합니다.
        github_token = os.environ.get("GH_PAT_TOKEN")
        repo_owner = "junghongseo"
        repo_name = "wysh_ritual"
        workflow_id = "ritual_engine.yml" # 파일명 기준
        
        if not github_token:
            response_data = json.dumps({"status": "error", "message": "GitHub 연동 토큰(GH_PAT_TOKEN)이 Vercel 환경변수에 설정되지 않았습니다."})
            self.wfile.write(response_data.encode('utf-8'))
            return

        api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/actions/workflows/{workflow_id}/dispatches"
        data = json.dumps({"ref": "main"}).encode('utf-8')
        
        req = urllib.request.Request(api_url, data=data, method="POST")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("Authorization", f"token {github_token}")
        req.add_header("User-Agent", "Wysh-Ritual-Vercel-App")
        
        try:
            with urllib.request.urlopen(req) as response:
                if response.status == 204:
                    response_data = json.dumps({"status": "success", "message": "성공! 클라우드로 작업 요청을 보냈습니다. 3분 뒤 노션에서 아티클을 확인하세요."})
                else:
                    response_data = json.dumps({"status": "error", "message": f"GitHub 트리거 실패 (상태 코드: {response.status})"})
        except urllib.error.HTTPError as e:
            response_data = json.dumps({"status": "error", "message": f"GitHub API 접속 거부: {e.code} - {e.reason}"})
        except Exception as e:
            response_data = json.dumps({"status": "error", "message": str(e)})
            
        # 3. 브라우저로 프론트 결과 반환 (단 1초 이내)
        self.wfile.write(response_data.encode('utf-8'))

    def do_OPTIONS(self):
        """CORS Preflight 요청 처리"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
