import os
import requests
import google.generativeai as genai
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from twilio.twiml.messaging_response import MessagingResponse
from gtts import gTTS
from pypdf import PdfReader
from dotenv import load_dotenv
from pymongo import MongoClient
from duckduckgo_search import DDGS

# --- Configuration ---
load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")
MONGO_URI = os.getenv("MONGO_URI")

if not API_KEY:
    raise ValueError("GOOGLE_API_KEY is missing in environment variables.")

# --- IDENTITY ---
SYSTEM_PROMPT = """
You are ThirdEye, an intelligent AI Assistant created by Rakesh Raushan.
Your core function is to assist with visual memory, daily tasks, and information retrieval.
Traits: Truthful, Direct, and Multilingual (Reply in the same language as the user).
"""

genai.configure(api_key=API_KEY)

# ⚠️ CRITICAL FIX: Switched to 1.5-flash for higher rate limits (1500 req/day)
# 2.5-flash has a limit of 20/day which caused your 429 error.
model = genai.GenerativeModel('gemini-1.5-flash', system_instruction=SYSTEM_PROMPT)

app = FastAPI()

# --- Database ---
photos_collection = None
if MONGO_URI:
    try:
        client = MongoClient(MONGO_URI, tls=True, tlsAllowInvalidCertificates=True)
        db = client.thirdeye_db
        photos_collection = db.photos
        print("INFO: Connected to MongoDB Atlas.")
    except Exception as e:
        print(f"ERROR: MongoDB Connection failed - {e}")

# --- Storage ---
BASE_DIR = Path("/tmp")
AUDIO_DIR = BASE_DIR / "audios"
DOCS_DIR = BASE_DIR / "documents"
for folder in [AUDIO_DIR, DOCS_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

app.mount("/audios", StaticFiles(directory=str(AUDIO_DIR)), name="audios")

# --- State ---
pending_image_context = {}
pdf_context = {}

# --- Utilities ---
def fetch_media_bytes(url: str) -> bytes:
    return requests.get(url).content

def search_internet(query: str) -> str:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
            if results:
                return "\n".join([f"- {r['body']}" for r in results])
    except Exception as e:
        print(f"Search failed: {e}")
    return None

def check_memory_duplicate(user_id: str, current_desc: str) -> str:
    if photos_collection is None:
        return None
        
    recent_items = photos_collection.find({"user_id": user_id}).sort("timestamp", -1).limit(20)
    
    for item in recent_items:
        prompt = f"""
        Compare these two object descriptions:
        1. New Image: "{current_desc}"
        2. Saved Memory: "{item['description']}"
        
        Are they referring to the EXACT SAME object? Answer ONLY 'YES' or 'NO'.
        """
        try:
            res = model.generate_content(prompt)
            if "YES" in res.text.strip().upper():
                return item['name_tag']
        except:
            continue
    return None

def extract_clean_name(user_text: str) -> str:
    prompt = f"""
    Extract ONLY the name tag from this user request: "{user_text}"
    Rules:
    - If user says "Save as Moti", return "Moti".
    - If user asks a question like "Did you save this?", return "Unknown".
    - Remove conversational filler.
    - Return strictly the name string.
    """
    try:
        res = model.generate_content(prompt)
        cleaned = res.text.strip()
        if "Unknown" in cleaned:
            return user_text # Fallback
        return cleaned
    except:
        return user_text

# --- Routes ---
@app.head("/")
async def health_check():
    return Response(status_code=200)

@app.post("/whatsapp")
async def handle_whatsapp(request: Request):
    form = await request.form()
    num_media = int(form.get('NumMedia', 0))
    msg_body = form.get('Body', '').strip()
    sender_id = form.get('From')
    host_url = str(request.base_url).replace("http://", "https://")
    resp = MessagingResponse()

    try:
        # === MEDIA ===
        if num_media > 0:
            media_type = form.get('MediaContentType0')
            media_url = form.get('MediaUrl0')
            media_data = fetch_media_bytes(media_url)

            if 'image' in media_type:
                # 1. Vision Analysis
                prompt = "Describe this image in detail. Identify the main object clearly."
                vision_res = model.generate_content([prompt, {"mime_type": media_type, "data": media_data}])
                description = vision_res.text.strip()

                # 2. Memory Check
                existing_tag = check_memory_duplicate(sender_id, description)

                if existing_tag:
                    resp.message(f"🧠 *Memory Recall:* I recognize this! It's '{existing_tag}'.")
                else:
                    pending_image_context[sender_id] = {"desc": description}
                    resp.message(f"👁️ *Analysis:* {description}\n\nThis seems new. Reply with a *Name* to save it.")

            elif 'application/pdf' in media_type:
                pdf_path = DOCS_DIR / f"doc_{sender_id[-4:]}.pdf"
                with open(pdf_path, "wb") as f: f.write(media_data)
                reader = PdfReader(pdf_path)
                pdf_context[sender_id] = "\n".join([p.extract_text() for p in reader.pages])
                resp.message(f"✅ PDF Processed ({len(reader.pages)} pages). Ask me questions.")

            elif 'audio' in media_type:
                audio_res = model.generate_content(["Listen and reply in the exact same language.", {"mime_type": media_type, "data": media_data}])
                reply_text = audio_res.text
                tts = gTTS(text=reply_text.replace('*', ''), lang='hi') 
                fn = f"reply_{datetime.now().strftime('%H%M%S')}.mp3"
                tts.save(str(AUDIO_DIR / fn))
                resp.message(f"🗣️ {reply_text}")
                resp.message("").media(f"{host_url}audios/{fn}")

        # === TEXT ===
        else:
            # 1. Saving a Name
            if sender_id in pending_image_context and photos_collection is not None:
                ctx = pending_image_context[sender_id]
                clean_name = extract_clean_name(msg_body)
                
                doc = {
                    "user_id": sender_id, 
                    "description": ctx['desc'], 
                    "name_tag": clean_name, 
                    "timestamp": datetime.now()
                }
                photos_collection.insert_one(doc)
                del pending_image_context[sender_id]
                resp.message(f"✅ Memory Saved: Tagged as '{clean_name}'.")

            # 2. PDF Context
            elif sender_id in pdf_context:
                resp.message(model.generate_content(f"Context: {pdf_context[sender_id]}\nUser: {msg_body}").text)

            # 3. Chat & Search
            else:
                web_data = ""
                if any(x in msg_body.lower() for x in ["price", "news", "weather", "who is"]) or "?" in msg_body:
                    res = search_internet(msg_body)
                    if res: web_data = f"Internet Info:\n{res}"

                # Fetch Memories
                mem_str = ""
                if photos_collection is not None:
                    recent = photos_collection.find({"user_id": sender_id}).sort("timestamp", -1).limit(3)
                    mem_str = ", ".join([f"{r['name_tag']} ({r['description']})" for r in recent])

                final_prompt = f"Memories: {mem_str}\nWeb Info: {web_data}\nUser: {msg_body}\nAnswer naturally."
                resp.message(model.generate_content(final_prompt).text)

    except Exception as e:
        print(f"ERROR: {e}")
        resp.message("⚠️ Server busy (Quota/Network). Please try again in a moment.")

    return Response(content=str(resp), media_type="application/xml")