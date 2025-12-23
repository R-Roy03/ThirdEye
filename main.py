import os
import sqlite3
import requests
import google.generativeai as genai
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from twilio.twiml.messaging_response import MessagingResponse
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path

# --- 1. SETUP & CONFIGURATION ---

BASE_DIR = Path(__file__).resolve().parent
env_file = BASE_DIR / ".env"
load_dotenv(dotenv_path=env_file)

api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    raise ValueError("Key Missing")
genai.configure(api_key=api_key)

model = genai.GenerativeModel('gemini-flash-latest')

app = FastAPI()

# Images folder banao agar nahi hai
IMAGES_DIR = BASE_DIR / "images"
IMAGES_DIR.mkdir(exist_ok=True)

# Images ko publicly available karao (Future use ke liye)
app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")

# --- 2. DATABASE (UPDATED) ---

def init_db():
    db_path = BASE_DIR / "memory.db"
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    # Table me ab 'filename' aur 'user_tag' (naam) bhi hoga
    c.execute('''CREATE TABLE IF NOT EXISTS memories
                 (id INTEGER PRIMARY KEY, 
                  description TEXT, 
                  timestamp TEXT, 
                  filename TEXT, 
                  user_tag TEXT)''')
    conn.commit()
    conn.close()

init_db()

# --- 3. WHATSAPP LOGIC ---

@app.post("/whatsapp")
async def whatsapp_reply(request: Request):
    form = await request.form()
    num_media = int(form.get('NumMedia', 0))
    msg_body = form.get('Body', '').strip() # Lowercase baad me karenge taaki naam sahi rahe
    sender = form.get('From')
    
    # URL for hosting (Isse hum baad me photo wapas bhejenge)
    # Abhi ke liye ye Localhost hai
    host_url = str(request.base_url) 

    resp = MessagingResponse()
    reply = resp.message()

    # === SCENARIO A: PHOTO AAYI HAI ===
    if num_media > 0:
        media_url = form.get('MediaUrl0')
        content_type = form.get('MediaContentType0')

        if 'image' in content_type:
            try:
                # 1. Image Download & Save Locally
                img_data = requests.get(media_url).content
                
                # File ka naam banao (Timestamp ke sath)
                filename = f"img_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                file_path = IMAGES_DIR / filename
                
                with open(file_path, "wb") as f:
                    f.write(img_data)
                
                # 2. Gemini Analysis
                image_parts = [{"mime_type": content_type, "data": img_data}]
                prompt = "Describe this image in short detail. Focus on visual features."
                ai_response = model.generate_content([prompt, image_parts[0]])
                description = ai_response.text
                
                # 3. Save to DB (Naam abhi NULL hai)
                conn = sqlite3.connect(str(BASE_DIR / 'memory.db'))
                c = conn.cursor()
                time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                c.execute("INSERT INTO memories (description, timestamp, filename, user_tag) VALUES (?, ?, ?, ?)", 
                          (description, time_now, filename, None))
                conn.commit()
                conn.close()

                reply.body(f"✅ Photo save ho gayi!\n🔍 **Gemini:** {description}\n\n👉 **Ise naam dene ke liye likho:**\n'Ye [Naam] hai' (Jaise: 'Ye Chintu hai')")

            except Exception as e:
                print(f"Error: {e}")
                reply.body("Photo save nahi ho payi.")
        else:
            reply.body("Sirf Photo bhejo.")

    # === SCENARIO B: TEXT MESSAGE ===
    else:
        msg_lower = msg_body.lower()

        # 1. NAME TAGGING: "Ye [Name] hai"
        if msg_lower.startswith("ye ") and msg_lower.endswith(" hai"):
            # Naam nikalo (Ye aur Hai ke beech ka text)
            name_tag = msg_body[3:-4].strip() # Case sensitive rakhna hai (Rakesh vs rakesh)
            
            conn = sqlite3.connect(str(BASE_DIR / 'memory.db'))
            c = conn.cursor()
            
            # Latest photo dhundo jiska naam abhi set nahi hai
            c.execute("SELECT id FROM memories ORDER BY id DESC LIMIT 1")
            row = c.fetchone()
            
            if row:
                photo_id = row[0]
                c.execute("UPDATE memories SET user_tag = ? WHERE id = ?", (name_tag, photo_id))
                conn.commit()
                reply.body(f"👍 Done! Pichli photo ko maine **'{name_tag}'** naam se save kar liya.")
            else:
                reply.body("Koi photo mili nahi jise naam du. Pehle photo bhejo.")
            conn.close()

        # 2. SEARCH BY NAME: "[Name] dikhao"
        elif "dikhao" in msg_lower or "batao" in msg_lower:
            # Naam guess karo (msg me se 'dikhao' hata do)
            search_name = msg_lower.replace("dikhao", "").replace("batao", "").replace("k bare me", "").strip()
            
            conn = sqlite3.connect(str(BASE_DIR / 'memory.db'))
            c = conn.cursor()
            
            # Naam se dhundo (Partial match, jaise 'baby' search karne pe 'Cute Baby' mile)
            c.execute("SELECT description, filename, timestamp, user_tag FROM memories WHERE user_tag LIKE ? OR description LIKE ? ORDER BY id DESC LIMIT 1", 
                      (f'%{search_name}%', f'%{search_name}%'))
            row = c.fetchone()
            conn.close()

            if row:
                desc, fname, time, tag = row
                
                # Image wapas bhejne ki koshish (Localhost pe ye shayad fail ho)
                img_link = f"{host_url}images/{fname}"
                
                reply_text = f"🖼️ **Photo Mil Gayi!**\n🏷️ **Naam:** {tag}\n📅 **Date:** {time}\n📝 **Description:** {desc}"
                
                reply.body(reply_text)
                
                # NOTE: Ye line tab chalegi jab hum Server par honge
                # reply.media(img_link) 
            else:
                reply.body(f"❌ '{search_name}' naam ki koi photo nahi mili.")

        # 3. SPECIFIC HISTORY: "2nd photo"
        elif "photo" in msg_lower and any(char.isdigit() for char in msg_lower):
             # Number nikalo string se
             import re
             nums = re.findall(r'\d+', msg_lower)
             if nums:
                 idx = int(nums[0]) - 1 # User bolega 1, hum lenge 0
                 
                 conn = sqlite3.connect(str(BASE_DIR / 'memory.db'))
                 c = conn.cursor()
                 c.execute("SELECT description, user_tag, timestamp FROM memories ORDER BY id DESC LIMIT 5")
                 rows = c.fetchall()
                 conn.close()
                 
                 if 0 <= idx < len(rows):
                     r = rows[idx]
                     tag_display = r[1] if r[1] else "No Name"
                     reply.body(f"📸 **Photo #{idx+1}:**\n🏷️ {tag_display}\n📝 {r[0]}")
                 else:
                     reply.body("Itni photos toh abhi history me nahi hain.")

        # 4. NORMAL HISTORY
        elif 'history' in msg_lower:
            conn = sqlite3.connect(str(BASE_DIR / 'memory.db'))
            c = conn.cursor()
            c.execute("SELECT description, user_tag FROM memories ORDER BY id DESC LIMIT 5")
            rows = c.fetchall()
            conn.close()
            
            txt = "📚 **Recent Photos:**\n"
            for i, r in enumerate(rows):
                name = r[1] if r[1] else "Unknown"
                txt += f"{i+1}. {name} - {r[0][:30]}...\n"
            reply.body(txt)

        elif '/reset' in msg_lower:
             # Database saaf
             pass # (Purana code use kar lena agar chahiye)

        else:
            reply.body("Samajh nahi aaya. 'Ye X hai' likho naam dene ke liye.")

    return Response(content=str(resp), media_type="application/xml")