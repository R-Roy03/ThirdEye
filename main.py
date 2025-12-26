import os
import sqlite3
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

# --- 1. SETUP ---
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")

if not api_key:
    print("❌ API Key missing! Check .env file.")
else:
    genai.configure(api_key=api_key)

# 🏆 Free & Stable Model
model = genai.GenerativeModel('gemini-flash-latest')

app = FastAPI()

# --- 2. UPTIME ROBOT FIX (HEAD Request) ---
# Ye naya hissa hai jo UptimeRobot ko "OK" bolega
@app.head("/")
async def keep_alive():
    return Response(status_code=200)

# --- 3. CLOUD STORAGE SETUP ---
BASE_DIR = Path("/tmp")
IMAGES_DIR = BASE_DIR / "images"
AUDIO_DIR = BASE_DIR / "audios"
DOCS_DIR = BASE_DIR / "documents"
DB_PATH = BASE_DIR / "memory.db"

# Folders create karo
for folder in [IMAGES_DIR, AUDIO_DIR, DOCS_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

# Mount folders for Twilio access
app.mount("/audios", StaticFiles(directory=str(AUDIO_DIR)), name="audios")

# --- 4. DATABASE (Long Term Memory) ---
def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS photos
                 (user_id TEXT, description TEXT, name_tag TEXT, timestamp TEXT)''')
    conn.commit()
    conn.close()

init_db()

# --- 5. RAM MEMORY (Short Term) ---
pending_names = {}  
pdf_context = {}    

# --- 6. HELPER FUNCTION ---
def clean_text_for_audio(text):
    return text.replace('*', '').replace('#', '').replace('_', '').replace('-', ' ')

# --- 7. MAIN LOGIC ---
@app.get("/")
async def root():
    return {"status": "Puch AI Final Version Live", "db": str(DB_PATH)}

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
                print("📸 Image processing...")
                img_data = requests.get(media_url).content
                
                safety_settings = [
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                ]
                
                image_part = {"mime_type": content_type, "data": img_data}
                prompt = "Describe this image in detail. Focus on people, faces, objects. Keep it factual."
                ai_response = model.generate_content([prompt, image_part], safety_settings=safety_settings)
                
                pending_names[sender_id] = ai_response.text
                resp.message("👁️ Maine dekh liya. Isse kis naam se save karu? (Naam likh kar bhejein)")

            # 2. PDF 📄
            elif 'application/pdf' in content_type:
                print("📄 PDF processing...")
                pdf_data = requests.get(media_url).content
                pdf_path = DOCS_DIR / f"doc_{sender_id[-4:]}_{datetime.now().strftime('%S')}.pdf"
                
                with open(pdf_path, "wb") as f:
                    f.write(pdf_data)
                
                reader = PdfReader(pdf_path)
                text_content = ""
                for page in reader.pages:
                    text_content += page.extract_text() + "\n"
                
                pdf_context[sender_id] = text_content
                resp.message(f"✅ PDF Received! Maine {len(reader.pages)} pages padh liye hain. Puchiye kya puchna hai?")

            # 3. AUDIO 🎙️
            elif 'audio' in content_type:
                print("🎙️ Audio processing...")
                audio_data = requests.get(media_url).content
                audio_part = {"mime_type": content_type, "data": audio_data}
                
                prompt = "Listen to the audio. Reply in the EXACT SAME LANGUAGE and TONE as the speaker (Hindi, English, or Hinglish)."
                ai_response = model.generate_content([prompt, audio_part])
                bot_text = ai_response.text
                
                resp.message(f"🗣️ {bot_text}")
                
                clean_text = clean_text_for_audio(bot_text)
                tts = gTTS(text=clean_text, lang='hi') 
                audio_fn = f"reply_{datetime.now().strftime('%H%M%S')}.mp3"
                tts.save(str(AUDIO_DIR / audio_fn))
                
                msg = resp.message("")
                msg.media(f"{host_url}audios/{audio_fn}")

        # === B. TEXT CHAT ===
        else:
            print(f"📩 Text: {msg_body}")

            # 1. NAME SAVING LOGIC
            if sender_id in pending_names:
                name_to_save = msg_body
                description = pending_names[sender_id]
                
                conn = sqlite3.connect(str(DB_PATH))
                c = conn.cursor()
                c.execute("INSERT INTO photos (user_id, description, name_tag, timestamp) VALUES (?, ?, ?, ?)",
                          (sender_id, description, name_to_save, str(datetime.now())))
                conn.commit()
                conn.close()
                
                del pending_names[sender_id]
                resp.message(f"✅ Done! Maine yaad kar liya ki ye **{name_to_save}** hai.")

            # 2. PDF Q&A LOGIC
            elif sender_id in pdf_context:
                context = pdf_context[sender_id]
                prompt = f"Document Context: {context}\nUser Question: {msg_body}\nINSTRUCTION: Answer based ONLY on the document in the SAME LANGUAGE as the User Question."
                ai_response = model.generate_content(prompt)
                resp.message(ai_response.text)

            # 3. NORMAL CHAT + MEMORY LOGIC
            else:
                conn = sqlite3.connect(str(DB_PATH))
                c = conn.cursor()
                c.execute("SELECT name_tag, description FROM photos WHERE user_id=? ORDER BY rowid DESC LIMIT 5", (sender_id,))
                rows = c.fetchall()
                conn.close()
                
                memory_text = "My Visual Memories:\n"
                if rows:
                    for r in rows:
                        memory_text += f"- Name: {r[0]}, Appearance: {r[1]}\n"
                
                prompt = f"{memory_text}\nUser Message: {msg_body}\nINSTRUCTION: Reply in the EXACT SAME LANGUAGE as the User Message (Hindi, English, or Hinglish). If asked about a person, use Visual Memories."
                ai_response = model.generate_content(prompt)
                resp.message(ai_response.text)

    except Exception as e:
        print(f"❌ Error: {e}")
        resp.message("Bot is waking up... Please ask again in 10 seconds.")

    return Response(content=str(resp), media_type="application/xml")