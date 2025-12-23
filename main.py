import os
import sqlite3
import requests
import re
import google.generativeai as genai
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from twilio.twiml.messaging_response import MessagingResponse
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path
from gtts import gTTS
from pypdf import PdfReader

# --- 1. SETUP ---
BASE_DIR = Path(__file__).resolve().parent
env_file = BASE_DIR / ".env"
load_dotenv(dotenv_path=env_file)

api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    raise ValueError("Key Missing! .env check karo.")
genai.configure(api_key=api_key)

# Model
model = genai.GenerativeModel('gemini-flash-latest')

app = FastAPI()

# Folders Setup
IMAGES_DIR = BASE_DIR / "images"
AUDIO_DIR = BASE_DIR / "audios"
DOCS_DIR = BASE_DIR / "documents"
IMAGES_DIR.mkdir(exist_ok=True)
AUDIO_DIR.mkdir(exist_ok=True)
DOCS_DIR.mkdir(exist_ok=True)

app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")
app.mount("/audios", StaticFiles(directory=AUDIO_DIR), name="audios")

# --- DATABASE ---
def init_db():
    db_path = BASE_DIR / "memory.db"
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS memories
                 (id INTEGER PRIMARY KEY, description TEXT, timestamp TEXT, filename TEXT, user_tag TEXT)''')
    conn.commit()
    conn.close()

init_db()

# --- HELPER FUNCTIONS ---
def clean_text_for_audio(text):
    clean = text.replace('*', '').replace('_', '').replace('#', '')
    clean = re.sub(r'[^\w\s\u0900-\u097F,?.!]', '', clean)
    return clean.strip()

def extract_text_from_pdf(pdf_path):
    try:
        reader = PdfReader(pdf_path)
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
        return text
    except Exception as e:
        print(f"PDF Error: {e}")
        return ""

def save_latest_doc_content(text):
    with open(DOCS_DIR / "latest_doc_context.txt", "w", encoding="utf-8") as f:
        f.write(text)

def get_latest_doc_content():
    try:
        path = DOCS_DIR / "latest_doc_context.txt"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    except:
        pass
    return ""

# --- WHATSAPP LOGIC ---
@app.post("/whatsapp")
async def whatsapp_reply(request: Request):
    form = await request.form()
    num_media = int(form.get('NumMedia', 0))
    msg_body = form.get('Body', '').strip()
    host_url = str(request.base_url)

    resp = MessagingResponse()

    # === A. MEDIA HANDLING ===
    if num_media > 0:
        media_url = form.get('MediaUrl0')
        content_type = form.get('MediaContentType0')
        
        # 1. PHOTO
        if 'image' in content_type:
            try:
                img_data = requests.get(media_url).content
                filename = f"img_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                with open(IMAGES_DIR / filename, "wb") as f:
                    f.write(img_data)
                
                image_parts = [{"mime_type": content_type, "data": img_data}]
                ai_response = model.generate_content(["Describe this image specifically.", image_parts[0]])
                description = ai_response.text
                
                conn = sqlite3.connect(str(BASE_DIR / 'memory.db'))
                c = conn.cursor()
                time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                c.execute("INSERT INTO memories (description, timestamp, filename, user_tag) VALUES (?, ?, ?, ?)", 
                          (description, time_now, filename, None))
                conn.commit()
                conn.close()
                resp.message(f"✅ Photo Save: {description}")
            except:
                resp.message("Error saving image.")

        # 2. AUDIO (WITH PDF CONTEXT)
        elif 'audio' in content_type:
            try:
                audio_data = requests.get(media_url).content
                audio_part = {"mime_type": content_type, "data": audio_data}
                
                doc_context = get_latest_doc_content()
                if doc_context:
                    prompt = f"""
                    Context from document:
                    ---
                    {doc_context[:30000]} 
                    ---
                    User Audio Question. Reply in Hinglish based on context.
                    """
                else:
                    prompt = "Listen to audio. Reply in Hinglish. Keep it short."

                ai_response = model.generate_content([prompt, audio_part])
                bot_text_reply = ai_response.text
                
                resp.message(f"🗣️ {bot_text_reply}")
                
                clean_reply = clean_text_for_audio(bot_text_reply)
                tts = gTTS(text=clean_reply, lang='hi', slow=False)
                audio_filename = f"reply_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"
                tts.save(str(AUDIO_DIR / audio_filename))
                
                msg2 = resp.message("")
                msg2.media(f"{host_url}audios/{audio_filename}")
            except:
                resp.message("Audio error.")

        # 3. PDF
        elif 'application/pdf' in content_type:
            try:
                resp.message("📄 Padh raha hu...")
                pdf_data = requests.get(media_url).content
                filename = f"doc_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
                pdf_path = DOCS_DIR / filename
                with open(pdf_path, "wb") as f:
                    f.write(pdf_data)
                
                full_text = extract_text_from_pdf(pdf_path)
                if full_text:
                    save_latest_doc_content(full_text)
                    resp.message(f"📚 PDF Saved! Ab sawal pucho.")
                else:
                    resp.message("❌ PDF empty hai.")
            except:
                resp.message("PDF error.")

    # === B. TEXT HANDLING ===
    else:
        doc_context = get_latest_doc_content()
        if doc_context:
            prompt = f"Context: {doc_context[:30000]}\nUser: {msg_body}\nAnswer in Hinglish."
        else:
            prompt = msg_body

        ai_response = model.generate_content(prompt)
        resp.message(ai_response.text)

    return Response(content=str(resp), media_type="application/xml")