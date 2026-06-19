import os
import sys
import subprocess
import re
import json
import urllib.request
import html as html_parser

# 필수 패키지 자동 설치 및 검사
try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
    from google import genai
    from google.genai import types
    from youtube_comment_downloader import YoutubeCommentDownloader
except ImportError:
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "--upgrade", "--quiet",
        "fastapi", "uvicorn", "pydantic", "google-genai", "youtube-comment-downloader"
    ])
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
    from google import genai
    from google.genai import types
    from youtube_comment_downloader import YoutubeCommentDownloader

app = FastAPI(title="유튜브 댓글 AI 종합 분석 API 서버")

# CORS 설정: GitHub Pages(프론트엔드)에서 이 서버로 접속할 수 있도록 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AnalysisRequest(BaseModel):
    api_key: str
    url: str
    sort_by: int  # 0: 인기순, 1: 최신순

def get_video_info(youtube_url):
    try:
        req = urllib.request.Request(youtube_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html = response.read().decode('utf-8')
        
        title_match = re.search(r'<meta name="title" content="([^"]+)"', html)
        channel_match = re.search(r'<link itemprop="name" content="([^"]+)"', html)
        
        title = title_match.group(1) if title_match else "알 수 없는 제목"
        channel = channel_match.group(1) if channel_match else "알 수 없는 채널"
        
        return html_parser.unescape(title), html_parser.unescape(channel)
    except Exception:
        return "정보를 조회할 수 없음", "정보를 조회할 수 없음"

@app.post("/api/analyze")
async def analyze_comments(request: AnalysisRequest):
    try:
        video_title, channel_name = get_video_info(request.url)
        if not video_title:
            video_title = "유튜브 여론 분석 대상 영상"
        if not channel_name:
            channel_name = "유튜브 크리에이터 채널"
        
        # 댓글 크롤링 (사용자가 선택한 정렬 방식 반영)
        downloader = YoutubeCommentDownloader()
        comments = downloader.get_comments_from_url(request.url, sort_by=request.sort_by)
        
        comment_list = []
        for i, comment in enumerate(comments):
        raw_text = comment.get('text') or ""
        text = raw_text.strip()
    
        if len(text) > 250:
            text = text[:250] + "...(중략)"
        comment_list.append(text)
        if i >= 79:
            break
            if len(text) > 250: 
                text = text[:250] + "...(중략)"
            comment_list.append(text)
            if i >= 79:  # 속도와 안정성을 위해 상위 80개 제한
                break
                
        if not comment_list:
            raise HTTPException(status_code=400, detail="댓글을 추출하지 못했습니다. URL을 확인해 주세요.")

        all_comments_text = "\n".join([f"- {c}" for c in comment_list])
        
        # Gemini API 호출 설정
        client = genai.Client(api_key=request.api_key)
        
        prompt = (
            f"너는 유튜브 빅데이터 여론 분석가야. 주어진 정보를 바탕으로 맞춤형 리포트를 작성하고, 감정 통계 데이터를 JSON 형태로 반환해줘.\n\n"
            f"[영상 정보]\n- 채널: {channel_name}\n- 제목: {video_title}\n\n"
            f"[지시 사항]\n"
            f"1. 구글 실시간 검색 기능(Google Search Tool)을 활성화하여 이 영상 및 채널의 카테고리와 배경 이슈를 파악할 것.\n"
            f"2. 파악한 장르적 특성에 맞춰 80개의 댓글 데이터를 심층 분석할 것.\n"
            f"3. 응답은 오직 아래 명시된 [JSON 출력 양식]만 마크다운 코드 블록 안에 담아 출력하고, 그 외의 불필요한 설명글은 일절 배제할 것.\n\n"
            f"[JSON 출력 양식]\n"
            f"```json\n"
            f"{{\n"
            f"  \"report\": \"## 유튜브 콘텐츠 맞춤 분석 리포트\\n### 1. 영상 성격 및 핵심 배경\\n(검색 기반 영상 성격 정의)\\n### 2. 전체 여론 분석\\n(시청자들의 전반적인 감정선 요약)\\n### 3. 주요 반응 및 핵심 키워드 3가지\\n- 내용1\\n- 내용2\\n- 내용3\\n### 4. 시청자 반응 3줄 요약\\n- 요약1\\n- 요약2\\n- 요약3\",\n"
            f"  \"positive\": 긍정퍼센트_숫자만,\n"
            f"  \"neutral\": 중립퍼센트_숫자만,\n"
            f"  \"negative\": 부정퍼센트_숫자만\n"
            f"}}\n"
            f"```\n\n"
            f"[댓글 데이터]\n{all_comments_text}"
        )
        
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
        )
        
        # JSON 데이터 파싱
        json_match = re.search(r"```json\s*([\s\S]*?)\s*```", response.text)
        if json_match:
            result_data = json.loads(json_match.group(1))
        else:
            cleaned_text = response.text.strip()
            if cleaned_text.startswith("{") and cleaned_text.endswith("}"):
                result_data = json.loads(cleaned_text)
            else:
                raise Exception("AI가 정형화된 JSON 데이터를 생성하는 데 실패했습니다.")
                
        return {
            "title": video_title,
            "channel": channel_name,
            "report": result_data.get("report", response.text),
            "chart_data": {
                "positive": result_data.get("positive", 40),
                "neutral": result_data.get("neutral", 30),
                "negative": result_data.get("negative", 30)
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
