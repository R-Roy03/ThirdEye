import os
import base64
import requests
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
from groq import Groq

# --- Configuration ---
load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MONGO_URI = os.getenv("MONGO_URI")

if not GROQ_API_KEY:
    print("⚠️ WARNING: GROQ_API_KEY missing.")

# Initialize Groq
client = Groq(api_key=GROQ_API_KEY)

# Models (Free & Fast)
TEXT_MODEL = "llama3-70b-8192"
VISION_MODEL = "llama-3.2-11b-vision-preview"
AUDIO_MODEL = "whisper-large-v3"

app = FastAPI()

# --- Database ---
photos_collection = None
if MONGO_URI:
    try:
        mongo_client = MongoClient(MONGO_URI, tls=True, tlsAllowInvalidCertificates=True)
        db = mongo_client.thirdeye_db
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
            if results: return "\n".join([f"- {r['body']}" for r in results])
    except: return None

def groq_chat(prompt: str, system_msg: str = "You are ThirdEye AI. Reply in the same language as the user.") -> str:
    try:
        return client.chat.completions.create(
            messages=[{"role": "system", "content": system_msg}, {"role": "user", "content": prompt}],
            model=TEXT_MODEL,
        ).choices[0].message.content
    except Exception as e: return f"Error: {e}"

def groq_vision(prompt: str, image_bytes: bytes) -> str:
    try:
        b64_img = base64.b64encode(image_bytes).decode('utf-8')
        return client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                ]
            }]
        ).choices[0].message.content
    except Exception as e: return f"Error: {e}"

def groq_transcribe(audio_bytes: bytes) -> str:
    try:
        temp_path = AUDIO_DIR / "temp_input.mp3"
        with open(temp_path, "wb") as f: f.write(audio_bytes)
        with open(temp_path, "rb") as file:
            return client.audio.transcriptions.create(
                file=(str(temp_path), file.read()),
                model=AUDIO_MODEL,
                response_format="text"
            )
    except Exception as e: return f"Error: {e}"

# --- Routes ---
@app.head("/")
async def health(): return Response(status_code=200)

@app.post("/whatsapp")
async def whatsapp(request: Request):
    form = await request.form()
    num_media = int(form.get('NumMedia', 0))
    msg = form.get('Body', '').strip()
    sender = form.get('From')
    host_url = str(request.base_url).replace("http://", "https://")
    resp = MessagingResponse()

    try:
        # === MEDIA ===
        if num_media > 0:
            m_type = form.get('MediaContentType0')
            m_url = form.get('MediaUrl0')
            m_data = fetch_media_bytes(m_url)

            if 'image' in m_type:
                # 1. Vision Analysis
                desc = groq_vision("Describe this image in 1 sentence. Identify the main object.", m_data)
                
                # 2. Memory Check (FIXED HERE)
                found_tag = None
                if photos_collection is not None:
                    recent = photos_collection.find({"user_id": sender}).limit(20)
                    for item in recent:
                        check = groq_chat(f"Compare:\n1. '{desc}'\n2. '{item['description']}'\nSame object? YES/NO ONLY.")
                        if "YES" in check.upper():
                            found_tag = item['name_tag']
                            break
                
                if found_tag:
                    resp.message(f"🧠 *Recall:* That's '{found_tag}'!")
                else:
                    pending_image_context[sender] = {"desc": desc}
                    resp.message(f"👁️ *Analysis:* {desc}\n\nReply with a *Name* to save this.")

            elif 'application/pdf' in m_type:
                path = DOCS_DIR / f"doc_{sender[-4:]}.pdf"
                with open(path, "wb") as f: f.write(m_data)
                reader = PdfReader(path)
                pdf_context[sender] = "\n".join([p.extract_text() for p in reader.pages])
                resp.message(f"✅ PDF Loaded. Ask questions.")

            elif 'audio' in m_type:
                user_text = groq_transcribe(m_data)
                ai_reply = groq_chat(f"User said: {user_text}. Reply naturally in the same language.")
                
                tts = gTTS(text=ai_reply.replace('*', ''), lang='hi')
                fn = f"reply_{datetime.now().strftime('%H%M%S')}.mp3"
                tts.save(str(AUDIO_DIR / fn))
                
                resp.message(f"🗣️ {ai_reply}")
                resp.message("").media(f"{host_url}audios/{fn}")

        # === TEXT ===
        else:
            # 1. Save Name
            if sender in pending_image_context:
                ctx = pending_image_context[sender]
                clean = groq_chat(f"Extract ONLY the name from: '{msg}'. If not a name, say 'Unknown'.").strip()
                final_name = msg if "Unknown" in clean else clean
                
                # (FIXED HERE)
                if photos_collection is not None:
                    photos_collection.insert_one({
                        "user_id": sender, "description": ctx['desc'], 
                        "name_tag": final_name, "timestamp": datetime.now()
                    })
                del pending_image_context[sender]
                resp.message(f"✅ Saved as '{final_name}'.")

            # 2. Chat
            else:
                web_info = ""
                if "?" in msg:
                    s = search_internet(msg)
                    if s: web_info = f"Web Info: {s}"
                
                memories = ""
                # (FIXED HERE)
                if photos_collection is not None:
                    recent = photos_collection.find({"user_id": sender}).limit(3)
                    memories = ", ".join([r['name_tag'] for r in recent])

                ans = groq_chat(f"Memories: {memories}\nWeb: {web_info}\nUser: {msg}")
                resp.message(ans)

    except Exception as e:
        print(f"Error: {e}")
        resp.message("⚠️ Bot sleeping.")

    return Response(content=str(resp), media_type="application/xml")