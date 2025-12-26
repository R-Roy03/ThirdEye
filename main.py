import os
import requests
import google.generativeai as genai
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from twilio.twiml.messaging_response import MessagingResponse
from datetime import datetime
from pathlib import Path
from gtts import gTTS
from pypdf import PdfReader
from dotenv import load_dotenv
from pymongo import MongoClient
from duckduckgo_search import DDGS # New Internet Tool

# --- 1. SETUP & CONFIGURATION ---
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")
mongo_uri = os.getenv("MONGO_URI")

# API Key Check
if not api_key:
    print("❌ Google API Key missing!")

genai.configure(api_key=api_key)
model = genai.GenerativeModel('gemini-flash-latest')

app = FastAPI()

# --- 2. CLOUD DATABASE CONNECTION (With SSL Fix) ---
photos_collection = None 

if mongo_uri:
    try:
        # SSL Fix applied here
        client = MongoClient(mongo_uri, tls=True, tlsAllowInvalidCertificates=True)
        db = client.thirdeye_db
        photos_collection = db.photos
        client.admin.command('ping')
        print("✅ Connected to MongoDB Cloud! (SSL Fix Applied)")
    except Exception as e:
        print(f"❌ MongoDB Connection Failed: {e}")
        print("⚠️ Bot running in Safe Mode.")
else:
    print("⚠️ MONGO_URI missing.")

# --- 3. FILES SETUP ---
BASE_DIR = Path("/tmp")
AUDIO_DIR = BASE_DIR / "audios"
DOCS_DIR = BASE_DIR / "documents"

for folder in [AUDIO_DIR, DOCS_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

app.mount("/audios", StaticFiles(directory=str(AUDIO_DIR)), name="audios")

# --- 4. MEMORY ---
pending_names = {}
pdf_context = {}

# --- 5. HELPER FUNCTIONS ---
def clean_text_for_audio(text):
    return text.replace('*', '').replace('#', '').replace('_', '').replace('-', ' ')

def google_search(query):
    """Internet par search karne ka function"""
    try:
        print(f"🌍 Searching for: {query}")
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
            if results:
                summary = "\n".join([f"- {r['title']}: {r['body']}" for r in results])
                return summary
    except Exception as e:
        print(f"Search Error: {e}")
    return None

# --- 6. UPTIME FIX ---
@app.head("/")
async def keep_alive():
    return Response(status_code=200)

@app.get("/")
async def root():
    return {"status": "Puch AI (Internet Enabled) Live 🌍"}

# --- 7. MAIN LOGIC ---
@app.post("/whatsapp")
async def whatsapp_reply(request: Request):
    form = await request.form()
    num_media = int(form.get('NumMedia', 0))
    msg_body = form.get('Body', '').strip()
    sender_id = form.get('From')
    
    host_url = str(request.base_url).replace("http://", "https://")
    resp = MessagingResponse()

    try:
        # === A. MEDIA HANDLING ===
        if num_media > 0:
            media_url = form.get('MediaUrl0')
            content_type = form.get('MediaContentType0')
            
            # 1. IMAGE 📸
            if 'image' in content_type:
                img_data = requests.get(media_url).content
                image_part = {"mime_type": content_type, "data": img_data}
                prompt = "Describe this image in detail."
                ai_response = model.generate_content([prompt, image_part])
                pending_names[sender_id] = ai_response.text
                resp.message("👁️ Maine dekh liya. Naam bataiye save karne ke liye?")

            # 2. PDF 📄
            elif 'application/pdf' in content_type:
                pdf_data = requests.get(media_url).content
                pdf_path = DOCS_DIR / f"doc_{sender_id[-4:]}.pdf"
                with open(pdf_path, "wb") as f:
                    f.write(pdf_data)
                reader = PdfReader(pdf_path)
                text_content = ""
                for page in reader.pages:
                    text_content += page.extract_text() + "\n"
                pdf_context[sender_id] = text_content
                resp.message(f"✅ PDF Read! {len(reader.pages)} pages.")

            # 3. AUDIO 🎙️
            elif 'audio' in content_type:
                audio_data = requests.get(media_url).content
                audio_part = {"mime_type": content_type, "data": audio_data}
                prompt = "Reply in same language/tone."
                ai_response = model.generate_content([prompt, audio_part])
                bot_text = ai_response.text
                
                resp.message(f"🗣️ {bot_text}")
                
                # TTS
                tts = gTTS(text=clean_text_for_audio(bot_text), lang='hi')
                audio_fn = f"reply_{datetime.now().strftime('%H%M%S')}.mp3"
                tts.save(str(AUDIO_DIR / audio_fn))
                msg = resp.message("")
                msg.media(f"{host_url}audios/{audio_fn}")

        # === B. TEXT CHAT (With INTERNET) ===
        else:
            # 1. NAME SAVING
            if sender_id in pending_names and photos_collection is not None:
                photo_doc = {
                    "user_id": sender_id, 
                    "description": pending_names[sender_id],
                    "name_tag": msg_body, 
                    "timestamp": datetime.now()
                }
                photos_collection.insert_one(photo_doc)
                del pending_names[sender_id]
                resp.message(f"✅ Saved memory: {msg_body}")

            # 2. PDF CONTEXT
            elif sender_id in pdf_context:
                prompt = f"Context: {pdf_context[sender_id]}\nUser: {msg_body}"
                ai_response = model.generate_content(prompt)
                resp.message(ai_response.text)

            # 3. NORMAL CHAT + MEMORY + INTERNET 🌍
            else:
                # Step 1: Check Database Memory
                memory_text = ""
                if photos_collection is not None:
                    recent = photos_collection.find({"user_id": sender_id}).sort("timestamp", -1).limit(3)
                    mems = [f"{r['name_tag']} ({r['description']})" for r in recent]
                    if mems: memory_text = "Memories: " + ", ".join(mems)

                # Step 2: Check for SEARCH Keywords
                search_result = ""
                # Agar user current info maange (news, weather, price, who is, latest)
                triggers = ["news", "price", "weather", "score", "latest", "current", "who is", "what is", "search", "update"]
                if any(word in msg_body.lower() for word in triggers):
                    search_data = google_search(msg_body)
                    if search_data:
                        search_result = f"\nINTERNET DATA:\n{search_data}\n"

                # Step 3: Final Prompt
                prompt = f"""
                {memory_text}
                {search_result}
                User Message: {msg_body}
                INSTRUCTION: Reply naturally. If Internet Data is provided, use it to answer accurately.
                """
                ai_response = model.generate_content(prompt)
                resp.message(ai_response.text)

    except Exception as e:
        print(f"Error: {e}")
        resp.message("Bot is thinking...")

    return Response(content=str(resp), media_type="application/xml")