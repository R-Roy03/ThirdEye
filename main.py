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

# --- 1. SETUP & CONFIGURATION ---
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")
mongo_uri = os.getenv("MONGO_URI")

# API Key Check
if not api_key:
    print("❌ Google API Key missing! Check Environment Variables.")

# Gemini Setup
genai.configure(api_key=api_key)
model = genai.GenerativeModel('gemini-flash-latest')

app = FastAPI()

# --- 2. CLOUD DATABASE CONNECTION (With SSL Fix) ---
photos_collection = None # Safety Lock (Bot crash nahi hone dega)

if mongo_uri:
    try:
        # 👇 YE HAI MAGIC LINE (SSL Fix)
        # Hum bol rahe hain: "Certificate invalid bhi ho toh chalega, bas connect ho ja."
        client = MongoClient(mongo_uri, tls=True, tlsAllowInvalidCertificates=True)
        
        db = client.thirdeye_db
        photos_collection = db.photos
        
        # Connection Test
        client.admin.command('ping')
        print("✅ Connected to MongoDB Cloud! (SSL Fix Applied)")
    except Exception as e:
        print(f"❌ MongoDB Connection Failed: {e}")
        print("⚠️ Bot is running in 'Safe Mode' (No Memory).")
else:
    print("⚠️ MONGO_URI missing in Environment Variables.")

# --- 3. TEMPORARY FILE STORAGE ---
BASE_DIR = Path("/tmp")
AUDIO_DIR = BASE_DIR / "audios"
DOCS_DIR = BASE_DIR / "documents"

# Folders banao
for folder in [AUDIO_DIR, DOCS_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

# Audio access ke liye link
app.mount("/audios", StaticFiles(directory=str(AUDIO_DIR)), name="audios")

# --- 4. SHORT TERM MEMORY (RAM) ---
pending_names = {}  # Photo bhejne ke baad naam puchne ke liye
pdf_context = {}    # PDF padhne ke baad sawal puchne ke liye

# --- 5. HELPER FUNCTION ---
def clean_text_for_audio(text):
    # Audio mein * ya # na bole, isliye safayi
    return text.replace('*', '').replace('#', '').replace('_', '').replace('-', ' ')

# --- 6. UPTIME ROBOT FIX ---
@app.head("/")
async def keep_alive():
    # UptimeRobot ko '200 OK' bolega taaki bot sowe na
    return Response(status_code=200)

@app.get("/")
async def root():
    # Browser par status dikhayega
    db_status = "Connected ✅" if photos_collection is not None else "Disconnected ❌"
    return {"status": "Puch AI Live", "database": db_status}

# --- 7. MAIN WHATSAPP LOGIC ---
@app.post("/whatsapp")
async def whatsapp_reply(request: Request):
    form = await request.form()
    num_media = int(form.get('NumMedia', 0))
    msg_body = form.get('Body', '').strip()
    sender_id = form.get('From')
    
    # Public URL for Audio files
    host_url = str(request.base_url).replace("http://", "https://")
    resp = MessagingResponse()

    try:
        # === A. MEDIA HANDLING (Photo/Audio/PDF) ===
        if num_media > 0:
            media_url = form.get('MediaUrl0')
            content_type = form.get('MediaContentType0')
            
            # 1. IMAGE 📸 (Vision)
            if 'image' in content_type:
                img_data = requests.get(media_url).content
                
                # Safety Filters (Taaki bot mana na kare)
                safety_settings = [
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                ]
                
                image_part = {"mime_type": content_type, "data": img_data}
                prompt = "Describe this image in detail. Focus on people (appearance), objects, and text. Keep it factual."
                ai_response = model.generate_content([prompt, image_part], safety_settings=safety_settings)
                
                # Description ko RAM mein save karo, user se naam pucho
                pending_names[sender_id] = ai_response.text
                resp.message("👁️ Maine dekh liya. Isse kis naam se save karu? (Naam likh kar bhejein)")

            # 2. PDF 📄 (Document Reader)
            elif 'application/pdf' in content_type:
                pdf_data = requests.get(media_url).content
                pdf_path = DOCS_DIR / f"doc_{sender_id[-4:]}_{datetime.now().strftime('%S')}.pdf"
                
                with open(pdf_path, "wb") as f:
                    f.write(pdf_data)
                
                # PDF Padhna
                reader = PdfReader(pdf_path)
                text_content = ""
                for page in reader.pages:
                    text_content += page.extract_text() + "\n"
                
                # Text ko RAM mein save karo
                pdf_context[sender_id] = text_content
                resp.message(f"✅ PDF Received! Maine {len(reader.pages)} pages padh liye hain. Ab isme se kuch bhi puchiye.")

            # 3. AUDIO 🎙️ (Voice Mode)
            elif 'audio' in content_type:
                audio_data = requests.get(media_url).content
                audio_part = {"mime_type": content_type, "data": audio_data}
                
                # Audio Sunna aur Jawab Sochna
                prompt = "Listen to the audio. Reply in the EXACT SAME LANGUAGE and TONE as the speaker."
                ai_response = model.generate_content([prompt, audio_part])
                bot_text = ai_response.text
                
                # Text Reply bhejo
                resp.message(f"🗣️ {bot_text}")
                
                # Audio Reply Banao (TTS)
                clean_text = clean_text_for_audio(bot_text)
                tts = gTTS(text=clean_text, lang='hi') # Default Hindi accent (works well for Hinglish too)
                audio_fn = f"reply_{datetime.now().strftime('%H%M%S')}.mp3"
                tts.save(str(AUDIO_DIR / audio_fn))
                
                # Audio Reply Bhejo
                msg = resp.message("")
                msg.media(f"{host_url}audios/{audio_fn}")

        # === B. TEXT CHAT HANDLING ===
        else:
            # 1. NAME SAVING (Agar photo bheji thi)
            if sender_id in pending_names:
                if photos_collection is not None:
                    name_to_save = msg_body
                    description = pending_names[sender_id]
                    
                    # MongoDB mein Save karna
                    photo_doc = {
                        "user_id": sender_id,
                        "description": description,
                        "name_tag": name_to_save,
                        "timestamp": datetime.now()
                    }
                    photos_collection.insert_one(photo_doc)
                    
                    del pending_names[sender_id]
                    resp.message(f"✅ Done! Maine hamesha ke liye yaad kar liya ki ye **{name_to_save}** hai.")
                else:
                    resp.message("⚠️ Database disconnected. Main abhi naam save nahi kar sakta.")

            # 2. PDF Q&A (Agar PDF bheji thi)
            elif sender_id in pdf_context:
                prompt = f"Context: {pdf_context[sender_id]}\nUser Question: {msg_body}\nAnswer based ONLY on the context."
                ai_response = model.generate_content(prompt)
                resp.message(ai_response.text)

            # 3. NORMAL CHAT + MEMORY RECALL
            else:
                memory_text = ""
                # Database se purani yaadein nikalo (Last 5 photos)
                if photos_collection is not None:
                    try:
                        recent_photos = photos_collection.find({"user_id": sender_id}).sort("timestamp", -1).limit(5)
                        memories = []
                        for doc in recent_photos:
                            memories.append(f"- Name: {doc['name_tag']}, Appearance: {doc['description']}")
                        if memories:
                            memory_text = "My Visual Memories:\n" + "\n".join(memories)
                    except Exception as e:
                        print(f"DB Read Error: {e}")

                # AI ko context aur memory do
                prompt = f"{memory_text}\nUser Message: {msg_body}\nINSTRUCTION: Reply smartly in the User's Language. If they ask 'Who is this?', use the Visual Memories."
                ai_response = model.generate_content(prompt)
                resp.message(ai_response.text)

    except Exception as e:
        print(f"❌ Critical Error: {e}")
        resp.message("System busy... Please try again in 5 seconds.")

    return Response(content=str(resp), media_type="application/xml")