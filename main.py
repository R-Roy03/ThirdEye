import os
import sqlite3
import requests
import re
import google.generativeai as genai
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from twilio.twiml.messaging_response import MessagingResponse
from datetime import datetime
from pathlib import Path
from gtts import gTTS
from pypdf import PdfReader
from dotenv import load_dotenv

# --- 1. SETUP & API KEY ---
# Pehle Cloud se key mangega, nahi mili to local .env check karega
api_key = os.environ.get("GOOGLE_API_KEY")
if not api_key:
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")

if not api_key:
    print("⚠️ WARNING: Google API Key nahi mili!")

genai.configure(api_key=api_key)
model = genai.GenerativeModel('gemini-flash-latest')

app = FastAPI()

# --- 2. CLOUD STORAGE SETUP (The Fix) ---
# Render par sirf /tmp folder writeable hota hai
BASE_DIR = Path("/tmp") 
IMAGES_DIR = BASE_DIR / "images"
AUDIO_DIR = BASE_DIR / "audios"
DOCS_DIR = BASE_DIR / "documents"

# Folders banao agar nahi hain
for folder in [IMAGES_DIR, AUDIO_DIR, DOCS_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

# Twilio ko files access karne dene ke liye mount karein
app.mount("/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")
app.mount("/audios", StaticFiles(directory=str(AUDIO_DIR)), name="audios")

# --- 3. DATABASE SETUP ---
def init_db():
    db_path = BASE_DIR / "memory.db"
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS memories
                 (id INTEGER PRIMARY KEY, description TEXT, timestamp TEXT, filename TEXT)''')
    conn.commit()
    conn.close()

init_db()

# --- 4. HELPER FUNCTIONS ---
def clean_text_for_audio(text):
    # Special characters hatana taaki audio saaf aaye
    clean = text.replace('*', '').replace('_', '').replace('#', '')
    return clean.strip()

# --- 5. ROUTES ---

@app.get("/")
async def root():
    return {"status": "Puch-AI is Live & Ready!", "storage": str(BASE_DIR)}

@app.post("/whatsapp")
async def whatsapp_reply(request: Request):
    form = await request.form()
    num_media = int(form.get('NumMedia', 0))
    msg_body = form.get('Body', '').strip()
    
    # Cloud URL (https zaroori hai)
    host_url = str(request.base_url).replace("http://", "https://")

    resp = MessagingResponse()

    try:
        # === A. MEDIA HANDLING (Photo/Audio/PDF) ===
        if num_media > 0:
            media_url = form.get('MediaUrl0')
            content_type = form.get('MediaContentType0')
            
            # 1. PHOTO 📸
            if 'image' in content_type:
                print("📸 Image mili...")
                img_data = requests.get(media_url).content
                filename = f"img_{datetime.now().strftime('%H%M%S')}.jpg"
                file_path = IMAGES_DIR / filename
                
                with open(file_path, "wb") as f:
                    f.write(img_data)
                
                # AI se pucho
                image_part = {"mime_type": content_type, "data": img_data}
                ai_response = model.generate_content(["Describe this image specifically.", image_part])
                description = ai_response.text
                
                # DB Save
                conn = sqlite3.connect(str(BASE_DIR / "memory.db"))
                c = conn.cursor()
                c.execute("INSERT INTO memories (description, timestamp, filename) VALUES (?, ?, ?)", 
                          (description, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), filename))
                conn.commit()
                conn.close()
                
                resp.message(f"👁️ Maine dekha: {description}")

            # 2. AUDIO 🎙️
            elif 'audio' in content_type:
                print("🎙️ Audio mila...")
                audio_data = requests.get(media_url).content
                audio_part = {"mime_type": content_type, "data": audio_data}
                
                ai_response = model.generate_content(["Listen and reply in Hinglish. Keep it short.", audio_part])
                bot_text = ai_response.text
                
                resp.message(f"🗣️ {bot_text}")
                
                # Text-to-Speech (Bot bolega)
                clean_reply = clean_text_for_audio(bot_text)
                tts = gTTS(text=clean_reply, lang='hi')
                audio_fn = f"reply_{datetime.now().strftime('%H%M%S')}.mp3"
                tts.save(str(AUDIO_DIR / audio_fn))
                
                # Audio wapas bhejo
                msg2 = resp.message("")
                msg2.media(f"{host_url}audios/{audio_fn}")

            # 3. PDF 📄
            elif 'application/pdf' in content_type:
                print("📄 PDF mili...")
                pdf_data = requests.get(media_url).content
                filename = f"doc_{datetime.now().strftime('%H%M%S')}.pdf"
                pdf_path = DOCS_DIR / filename
                
                with open(pdf_path, "wb") as f:
                    f.write(pdf_data)
                
                reader = PdfReader(pdf_path)
                text = ""
                for page in reader.pages:
                    text += page.extract_text()
                
                # Context save karne ka logic yahan add kar sakte hain
                resp.message(f"✅ PDF Received! Isme {len(reader.pages)} pages hain. Ab aap isse jude sawal puch sakte hain.")

        # === B. TEXT CHAT ===
        else:
            print(f"📩 Text aaya: {msg_body}")
            ai_response = model.generate_content(msg_body)
            resp.message(ai_response.text)

    except Exception as e:
        print(f"❌ Error: {e}")
        resp.message("System updating... Please try again in 1 minute.")

    return Response(content=str(resp), media_type="application/xml")