import os
import sqlite3
import requests
import re  # Text safai ke liye
import google.generativeai as genai
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from twilio.twiml.messaging_response import MessagingResponse
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path
from gtts import gTTS  # Bolne ke liye

# --- 1. SETUP ---
BASE_DIR = Path(__file__).resolve().parent
env_file = BASE_DIR / ".env"
load_dotenv(dotenv_path=env_file)

api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    raise ValueError("Key Missing! .env check karo.")
genai.configure(api_key=api_key)

# Stable Model use kar rahe hain
model = genai.GenerativeModel('gemini-flash-latest')

app = FastAPI()

# Folders Setup
IMAGES_DIR = BASE_DIR / "images"
AUDIO_DIR = BASE_DIR / "audios"
IMAGES_DIR.mkdir(exist_ok=True)
AUDIO_DIR.mkdir(exist_ok=True)

# Files ko Public Access dena
app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")
app.mount("/audios", StaticFiles(directory=AUDIO_DIR), name="audios")

# --- 2. DATABASE ---
def init_db():
    db_path = BASE_DIR / "memory.db"
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS memories
                 (id INTEGER PRIMARY KEY, 
                  description TEXT, 
                  timestamp TEXT, 
                  filename TEXT, 
                  user_tag TEXT)''')
    conn.commit()
    conn.close()

init_db()

# --- HELPER: TEXT CLEANER ---
def clean_text_for_audio(text):
    """Emojis aur Symbols hatayega taaki Audio saaf aaye"""
    # 1. Markdown hataya (*bold*)
    clean = text.replace('*', '').replace('_', '').replace('#', '')
    # 2. Emojis hataye (Sirf Text, Numbers aur Punctuation rakha)
    clean = re.sub(r'[^\w\s\u0900-\u097F,?.!]', '', clean)
    return clean.strip()

# --- 3. WHATSAPP LOGIC ---
@app.post("/whatsapp")
async def whatsapp_reply(request: Request):
    form = await request.form()
    num_media = int(form.get('NumMedia', 0))
    msg_body = form.get('Body', '').strip()
    
    # URL setup (Audio bhejne ke liye)
    host_url = str(request.base_url)

    resp = MessagingResponse()

    # === A. MEDIA HANDLING ===
    if num_media > 0:
        media_url = form.get('MediaUrl0')
        content_type = form.get('MediaContentType0')
        
        # 1. PHOTO AAYI HAI 📸
        if 'image' in content_type:
            try:
                # Image Download
                img_data = requests.get(media_url).content
                filename = f"img_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                with open(IMAGES_DIR / filename, "wb") as f:
                    f.write(img_data)
                
                # Gemini Vision
                image_parts = [{"mime_type": content_type, "data": img_data}]
                ai_response = model.generate_content(["Describe this image specifically.", image_parts[0]])
                description = ai_response.text
                
                # DB Save
                conn = sqlite3.connect(str(BASE_DIR / 'memory.db'))
                c = conn.cursor()
                time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                c.execute("INSERT INTO memories (description, timestamp, filename, user_tag) VALUES (?, ?, ?, ?)", 
                          (description, time_now, filename, None))
                conn.commit()
                conn.close()

                resp.message(f"✅ Photo Save: {description}\n\n👉 Naam dene ke liye likho: 'Ye [Naam] hai'")
            except Exception as e:
                resp.message("Error saving image.")

        # 2. AUDIO AAYA HAI 🎙️ -> 🗣️
        elif 'audio' in content_type:
            try:
                # Step A: Audio Download
                audio_data = requests.get(media_url).content
                audio_part = {"mime_type": content_type, "data": audio_data}
                
                # Step B: Gemini Process (Language Detection)
                prompt = """
                Listen to this audio.
                1. If user speaks Hindi, reply in Hindi.
                2. If English, reply in English.
                3. Keep it short and friendly.
                """
                ai_response = model.generate_content([prompt, audio_part])
                bot_text_reply = ai_response.text
                
                # MESSAGE 1: Pehle Text bhejo
                resp.message(f"🗣️ {bot_text_reply}")
                
                # Step C: Audio Generation (Clean Text se)
                clean_reply = clean_text_for_audio(bot_text_reply)
                
                # 'hi' (Hindi) engine use kar rahe hain jo Indian English bhi achi bolta hai
                tts = gTTS(text=clean_reply, lang='hi', slow=False)
                
                audio_filename = f"reply_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"
                tts.save(str(AUDIO_DIR / audio_filename))
                
                # MESSAGE 2: Phir Audio bhejo
                msg2 = resp.message("") # Empty text body for audio message
                audio_link = f"{host_url}audios/{audio_filename}"
                msg2.media(audio_link)

            except Exception as e:
                print(f"Audio Error: {e}")
                resp.message("Awaz samajh nahi aayi.")
        
        else:
            resp.message("Sirf Photo 📸 ya Audio 🎙️ bhejo.")

    # === B. TEXT HANDLING (Smart Gallery + Chat) ===
    else:
        msg_lower = msg_body.lower()

        # Name Tagging Logic
        if msg_lower.startswith("ye ") and msg_lower.endswith(" hai"):
            name_tag = msg_body[3:-4].strip()
            conn = sqlite3.connect(str(BASE_DIR / 'memory.db'))
            c = conn.cursor()
            c.execute("SELECT id FROM memories ORDER BY id DESC LIMIT 1")
            row = c.fetchone()
            if row:
                c.execute("UPDATE memories SET user_tag = ? WHERE id = ?", (name_tag, row[0]))
                conn.commit()
                resp.message(f"👍 Done! Photo ka naam **'{name_tag}'** rakh diya.")
            else:
                resp.message("Pehle photo to bhejo!")
            conn.close()

        # Photo Searching Logic
        elif "dikhao" in msg_lower or "batao" in msg_lower:
            search_name = msg_lower.replace("dikhao", "").replace("batao", "").replace("k bare me", "").strip()
            conn = sqlite3.connect(str(BASE_DIR / 'memory.db'))
            c = conn.cursor()
            c.execute("SELECT description, filename, timestamp, user_tag FROM memories WHERE user_tag LIKE ? OR description LIKE ? ORDER BY id DESC LIMIT 1", 
                      (f'%{search_name}%', f'%{search_name}%'))
            row = c.fetchone()
            conn.close()

            if row:
                desc, fname, time, tag = row
                img_link = f"{host_url}images/{fname}"
                resp.message(f"🖼️ **{tag}**\n📝 {desc}")
                # Note: Localhost pe photo phone pe shayad na dikhe
                # resp.message("").media(img_link) 
            else:
                resp.message(f"❌ '{search_name}' nahi mila.")
        
        # History Logic
        elif 'history' in msg_lower:
            conn = sqlite3.connect(str(BASE_DIR / 'memory.db'))
            c = conn.cursor()
            c.execute("SELECT description, user_tag FROM memories ORDER BY id DESC LIMIT 5")
            rows = c.fetchall()
            conn.close()
            txt = "📚 **Recent Photos:**\n"
            for i, r in enumerate(rows):
                name = r[1] if r[1] else "Unknown"
                txt += f"{i+1}. {name}: {r[0][:30]}...\n"
            resp.message(txt)

        # Normal Chat
        else:
            ai_response = model.generate_content(msg_body)
            resp.message(ai_response.text)

    return Response(content=str(resp), media_type="application/xml")