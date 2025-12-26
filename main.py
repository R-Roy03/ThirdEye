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

# --- 1. SETUP ---
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")
genai.configure(api_key=api_key)

# Free & Stable Model
model = genai.GenerativeModel('gemini-flash-latest')

app = FastAPI()

# --- 2. CLOUD STORAGE (Render Friendly) ---
BASE_DIR = Path("/tmp")
IMAGES_DIR = BASE_DIR / "images"
AUDIO_DIR = BASE_DIR / "audios"
DOCS_DIR = BASE_DIR / "documents"

for folder in [IMAGES_DIR, AUDIO_DIR, DOCS_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

# Twilio ko audio file sunane ke liye mount karna zaroori hai
app.mount("/audios", StaticFiles(directory=str(AUDIO_DIR)), name="audios")

# --- 3. MEMORY (RAM) ---
# PDF Context yaad rakhne ke liye
user_context = {}

# --- 4. HELPER FUNCTION ---
def clean_text_for_audio(text):
    # Emojis aur symbols hatata hai taaki awaz saaf aaye
    return text.replace('*', '').replace('#', '').replace('_', '')

# --- 5. MAIN LOGIC ---
@app.get("/")
async def root():
    return {"status": "Puch AI is Online", "memory": len(user_context)}

@app.post("/whatsapp")
async def whatsapp_reply(request: Request):
    form = await request.form()
    num_media = int(form.get('NumMedia', 0))
    msg_body = form.get('Body', '').strip()
    sender_id = form.get('From')
    
    # Cloud URL (Audio bhejne ke liye)
    host_url = str(request.base_url).replace("http://", "https://")
    resp = MessagingResponse()

    try:
        # === A. MEDIA HANDLING ===
        if num_media > 0:
            media_url = form.get('MediaUrl0')
            content_type = form.get('MediaContentType0')
            
            # 1. IMAGE 📸 (Smart Logic)
            if 'image' in content_type:
                print("📸 Image aayi...")
                img_data = requests.get(media_url).content
                
                # Safety Settings (Human images allowed)
                safety_settings = [
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                ]
                
                image_part = {"mime_type": content_type, "data": img_data}
                ai_response = model.generate_content(
                    ["Is image ko Hinglish mein describe karo. Agar koi insaan hai to batao wo kya kar raha hai.", image_part],
                    safety_settings=safety_settings
                )
                resp.message(f"👁️ {ai_response.text}")

            # 2. PDF 📄 (Memory Logic)
            elif 'application/pdf' in content_type:
                print("📄 PDF aayi...")
                pdf_data = requests.get(media_url).content
                pdf_path = DOCS_DIR / f"doc_{sender_id[-4:]}.pdf"
                
                with open(pdf_path, "wb") as f:
                    f.write(pdf_data)
                
                # Text Extract
                reader = PdfReader(pdf_path)
                text_content = ""
                for page in reader.pages:
                    text_content += page.extract_text() + "\n"
                
                # Save to Memory
                user_context[sender_id] = text_content
                resp.message(f"✅ PDF Received! Isme {len(reader.pages)} pages hain. Ab aap isse related koi bhi sawal puchiye.")

            # 3. AUDIO 🎙️ (Voice Reply Logic)
            elif 'audio' in content_type:
                print("🎙️ Audio aaya...")
                audio_data = requests.get(media_url).content
                audio_part = {"mime_type": content_type, "data": audio_data}
                
                # AI se text answer lo
                ai_response = model.generate_content(["Listen and reply in Hinglish. Keep it short and friendly.", audio_part])
                bot_text = ai_response.text
                
                # 1. Text bhejo
                resp.message(f"🗣️ {bot_text}")
                
                # 2. Audio Generate karo (TTS)
                clean_text = clean_text_for_audio(bot_text)
                tts = gTTS(text=clean_text, lang='hi')
                audio_filename = f"reply_{datetime.now().strftime('%H%M%S')}.mp3"
                tts.save(str(AUDIO_DIR / audio_filename))
                
                # 3. Audio file attach karo
                msg = resp.message("")
                msg.media(f"{host_url}audios/{audio_filename}")

        # === B. TEXT CHAT (Context Aware) ===
        else:
            print(f"📩 Text: {msg_body}")
            
            # Agar purana PDF context hai to use karo
            if sender_id in user_context:
                print("💡 Using PDF Memory")
                context = user_context[sender_id]
                prompt = f"Document Content:\n{context}\n\nUser Question: {msg_body}\nAnswer in Hinglish."
                ai_response = model.generate_content(prompt)
                resp.message(ai_response.text)
            else:
                # Normal Chat
                ai_response = model.generate_content(msg_body)
                resp.message(ai_response.text)

    except Exception as e:
        print(f"❌ Error: {e}")
        resp.message("System busy, please try again.")

    return Response(content=str(resp), media_type="application/xml")