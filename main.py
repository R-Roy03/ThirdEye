import os
import sqlite3
import requests
import google.generativeai as genai
from fastapi import FastAPI, Request, Response
from twilio.twiml.messaging_response import MessagingResponse
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path

# --- 1. SETUP & CONFIGURATION ---

# Path Tracing: Pata lagao main.py kahan hai, wahi .env milegi
BASE_DIR = Path(__file__).resolve().parent
env_file = BASE_DIR / ".env"

print(f"👀 Searching for secret key at: {env_file}")

# Load environment variables
load_dotenv(dotenv_path=env_file)

# API Key uthao
api_key = os.getenv("GOOGLE_API_KEY")

if not api_key:
    print("❌ Arre bhai! API Key nahi mili. .env file check karo.")
    # Agar key nahi mili to code yahi rok do
    raise ValueError("Key Missing")
else:
    print("✅ Mast! Key mil gayi. System ON hai.")
    genai.configure(api_key=api_key)

# Model set karte hain
# Agar 'gemini-flash-latest' error de, to 'gemini-1.5-flash' try karna
model = genai.GenerativeModel('gemini-flash-latest')

app = FastAPI()

# --- 2. DATABASE (BOT KA DIMAAG) ---

def init_db():
    """Simple database banayega agar nahi hai"""
    db_path = BASE_DIR / "memory.db"
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    # Table: ID, Description, Time
    c.execute('''CREATE TABLE IF NOT EXISTS memories
                 (id INTEGER PRIMARY KEY, description TEXT, timestamp TEXT)''')
    conn.commit()
    conn.close()

init_db()


# --- 3. WHATSAPP LOGIC ---

@app.post("/whatsapp")
async def whatsapp_reply(request: Request):
    # Form data nikalo
    form = await request.form()
    
    num_media = int(form.get('NumMedia', 0))
    msg_body = form.get('Body', '').strip().lower()
    sender = form.get('From')

    print(f"📩 New Message from {sender} | Photo: {num_media > 0}")

    # Jawab tayyar karte hain
    resp = MessagingResponse()
    reply = resp.message()

    # === CASE A: PHOTO AAYI HAI (VISION + MEMORY) ===
    if num_media > 0:
        media_url = form.get('MediaUrl0')
        content_type = form.get('MediaContentType0')

        if 'image' in content_type:
            reply.body("Ruko zara! Mai dekh raha hu aur yaad kar raha hu... 🧠")
            
            try:
                # 1. Photo download
                print("Downloading image...")
                img_data = requests.get(media_url).content
                image_parts = [{"mime_type": content_type, "data": img_data}]
                
                # 2. Gemini Analysis
                prompt = "Describe this image in short detail. Be witty."
                ai_response = model.generate_content([prompt, image_parts[0]])
                description = ai_response.text
                
                # 3. Save to DB
                conn = sqlite3.connect(str(BASE_DIR / 'memory.db'))
                c = conn.cursor()
                time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                c.execute("INSERT INTO memories (description, timestamp) VALUES (?, ?)", 
                          (description, time_now))
                conn.commit()
                conn.close()

                print(f"Saved to Memory: {description}")
                reply.body(f"✅ Maine yaad kar liya:\n🔍 {description}")

            except Exception as e:
                print(f"Gadbad ho gayi: {e}")
                reply.body("Sorry, server busy hai ya photo clear nahi hai.")
        else:
            reply.body("Bhai sirf Photo bhejo, Video nahi.")

    # === CASE B: TEXT AAYA HAI (HISTORY) ===
    else:
        if 'history' in msg_body or 'yaad' in msg_body:
            conn = sqlite3.connect(str(BASE_DIR / 'memory.db'))
            c = conn.cursor()
            # Last 5 memories
            c.execute("SELECT description, timestamp FROM memories ORDER BY id DESC LIMIT 5")
            data = c.fetchall()
            conn.close()

            if data:
                text_response = "🧠 **Meri Yaaddaasht (Last 5):**\n\n"
                for row in data:
                    text_response += f"⏰ {row[1]}\n👀 {row[0]}\n---\n"
                reply.body(text_response)
            else:
                reply.body("Abhi tak kuch yaad nahi kiya. Photo bhejo! 📸")
        
        elif 'hi' in msg_body or 'hello' in msg_body:
            reply.body("Namaste! 📸 Photo bhejo, mai yaad rakhunga.")
        else:
            reply.body("Photo bhejo ya 'History' likho.")

    return Response(content=str(resp), media_type="application/xml")