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

# --- 1. SETUP ---
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")
mongo_uri = os.getenv("MONGO_URI")

if not api_key:
    print("❌ Google API Key missing!")

genai.configure(api_key=api_key)
model = genai.GenerativeModel('gemini-flash-latest')

app = FastAPI()

# --- 2. CLOUD DATABASE CONNECTION (Safe Mode) ---
photos_collection = None  # Pehle se define kar diya taaki crash na ho

if mongo_uri:
    try:
        client = MongoClient(mongo_uri)
        db = client.thirdeye_db
        photos_collection = db.photos
        print("✅ Connected to MongoDB Cloud!")
    except Exception as e:
        print(f"❌ MongoDB Connection Failed: {e}")
        print("⚠️ Bot will run WITHOUT memory.")
else:
    print("⚠️ MONGO_URI missing in Environment Variables.")

# --- 3. FILES SETUP ---
BASE_DIR = Path("/tmp")
AUDIO_DIR = BASE_DIR / "audios"
DOCS_DIR = BASE_DIR / "documents"

for folder in [AUDIO_DIR, DOCS_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

app.mount("/audios", StaticFiles(directory=str(AUDIO_DIR)), name="audios")

# --- 4. RAM MEMORY ---
pending_names = {}
pdf_context = {}

def clean_text_for_audio(text):
    return text.replace('*', '').replace('#', '').replace('_', '').replace('-', ' ')

@app.head("/")
async def keep_alive():
    return Response(status_code=200)

@app.get("/")
async def root():
    status = "Online (Cloud Memory Active)" if photos_collection is not None else "Online (Memory Offline)"
    return {"status": status}

# --- 5. MAIN LOGIC ---
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
                pdf_data = requests.get(media_url).content
                pdf_path = DOCS_DIR / f"doc_{sender_id[-4:]}_{datetime.now().strftime('%S')}.pdf"
                with open(pdf_path, "wb") as f:
                    f.write(pdf_data)
                reader = PdfReader(pdf_path)
                text_content = ""
                for page in reader.pages:
                    text_content += page.extract_text() + "\n"
                pdf_context[sender_id] = text_content
                resp.message(f"✅ PDF Received! Maine {len(reader.pages)} pages padh liye hain.")

            # 3. AUDIO 🎙️
            elif 'audio' in content_type:
                audio_data = requests.get(media_url).content
                audio_part = {"mime_type": content_type, "data": audio_data}
                prompt = "Listen to the audio. Reply in the EXACT SAME LANGUAGE and TONE."
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
            # 1. NAME SAVING (Check if DB Connected)
            if sender_id in pending_names:
                if photos_collection is not None:
                    name_to_save = msg_body
                    description = pending_names[sender_id]
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
                    resp.message("⚠️ Database Error: Main abhi naam save nahi kar sakta. Password check karein.")

            # 2. PDF Q&A
            elif sender_id in pdf_context:
                prompt = f"Context: {pdf_context[sender_id]}\nUser: {msg_body}\nAnswer based on document."
                ai_response = model.generate_content(prompt)
                resp.message(ai_response.text)

            # 3. NORMAL CHAT + CLOUD MEMORY
            else:
                memory_text = ""
                if photos_collection is not None:
                    try:
                        recent_photos = photos_collection.find({"user_id": sender_id}).sort("timestamp", -1).limit(5)
                        memories = []
                        for doc in recent_photos:
                            memories.append(f"- Name: {doc['name_tag']}, Desc: {doc['description']}")
                        if memories:
                            memory_text = "My Visual Memories:\n" + "\n".join(memories)
                    except Exception as e:
                        print(f"DB Read Error: {e}")

                prompt = f"{memory_text}\nUser Message: {msg_body}\nReply in User's Language. Use memories if needed."
                ai_response = model.generate_content(prompt)
                resp.message(ai_response.text)

    except Exception as e:
        print(f"❌ Error: {e}")
        resp.message("System busy... Please try again.")

    return Response(content=str(resp), media_type="application/xml")