import os
import requests
import google.generativeai as genai
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from twilio.twiml.messaging_response import MessagingResponse
from gtts import gTTS
from pypdf import PdfReader
from dotenv import load_dotenv
from pymongo import MongoClient
from duckduckgo_search import DDGS

# --- Configuration ---
load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")
MONGO_URI = os.getenv("MONGO_URI")

# --- Initialize Services ---
if not API_KEY:
    raise ValueError("GOOGLE_API_KEY is missing in environment variables.")

# Identity & Behavior Configuration
SYSTEM_PROMPT = """
You are ThirdEye, an intelligent AI Assistant created by Rakesh Raushan.
Your core function is to assist with visual memory, daily tasks, and information retrieval.
Traits: Truthful, Direct, and Multilingual (Reply in the same language as the user).
"""

genai.configure(api_key=API_KEY)
# Using gemini-2.5-flash as per availability
model = genai.GenerativeModel('gemini-2.5-flash', system_instruction=SYSTEM_PROMPT)

app = FastAPI()

# --- Database Connection ---
photos_collection = None
if MONGO_URI:
    try:
        client = MongoClient(MONGO_URI, tls=True, tlsAllowInvalidCertificates=True)
        db = client.thirdeye_db
        photos_collection = db.photos
        print("INFO: Connected to MongoDB Atlas.")
    except Exception as e:
        print(f"ERROR: MongoDB Connection failed - {e}")

# --- File Storage ---
BASE_DIR = Path("/tmp")
AUDIO_DIR = BASE_DIR / "audios"
DOCS_DIR = BASE_DIR / "documents"

for folder in [AUDIO_DIR, DOCS_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

app.mount("/audios", StaticFiles(directory=str(AUDIO_DIR)), name="audios")

# --- In-Memory State ---
pending_image_context = {}
pdf_context = {}

# --- Utility Functions ---

def fetch_media_bytes(url: str) -> bytes:
    """Securely fetches media content from Twilio URL."""
    return requests.get(url).content

def search_internet(query: str) -> str:
    """Performs a web search using DuckDuckGo."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
            if results:
                return "\n".join([f"- {r['body']}" for r in results])
    except Exception as e:
        print(f"Search failed: {e}")
    return None

def check_memory_duplicate(user_id: str, current_desc: str) -> str:
    """
    Checks if a similar object exists in the user's recent memory.
    Returns the Name Tag if found, else None.
    """
    # FIX: Explicitly check for None
    if photos_collection is None:
        return None
        
    recent_items = photos_collection.find({"user_id": user_id}).sort("timestamp", -1).limit(20)
    
    for item in recent_items:
        # LLM Comparison for semantic matching
        prompt = f"""
        Compare these two descriptions:
        1. New Image: "{current_desc}"
        2. Saved Memory: "{item['description']}"
        
        Are they referring to the SAME object/person? Answer ONLY 'YES' or 'NO'.
        """
        try:
            res = model.generate_content(prompt)
            if "YES" in res.text.strip().upper():
                return item['name_tag']
        except:
            continue
    return None

def extract_clean_name(user_text: str) -> str:
    """
    Extracts the specific entity name from a conversational sentence.
    """
    prompt = f"""
    Extract ONLY the name tag from this user request: "{user_text}"
    Rules:
    - Remove conversational filler ("save as", "please", "naam se").
    - Return strictly the name string.
    """
    try:
        res = model.generate_content(prompt)
        return res.text.strip()
    except:
        return user_text

# --- Main Route ---

@app.head("/")
async def health_check():
    return Response(status_code=200)

@app.post("/whatsapp")
async def handle_whatsapp(request: Request):
    form = await request.form()
    num_media = int(form.get('NumMedia', 0))
    msg_body = form.get('Body', '').strip()
    sender_id = form.get('From')
    
    host_url = str(request.base_url).replace("http://", "https://")
    resp = MessagingResponse()

    try:
        # === MEDIA HANDLING ===
        if num_media > 0:
            media_type = form.get('MediaContentType0')
            media_url = form.get('MediaUrl0')
            media_data = fetch_media_bytes(media_url)

            # 1. Image Logic
            if 'image' in media_type:
                # Analyze
                prompt = "Describe this image in 1 short sentence. Identify the main object."
                vision_res = model.generate_content([prompt, {"mime_type": media_type, "data": media_data}])
                description = vision_res.text.strip()

                # Deduplication Check (Memory Recall)
                existing_tag = check_memory_duplicate(sender_id, description)

                if existing_tag:
                    resp.message(f"🧠 *Memory Recall:* I recognize this! It's '{existing_tag}'.")
                else:
                    # New Item -> Store context and ask for tag
                    pending_image_context[sender_id] = {
                        "desc": description,
                        "data": media_data # Storing binary briefly for context
                    }
                    resp.message(f"👁️ *Analysis:* {description}\n\nThis seems new. Reply with a *Name* to save it to memory.")

            # 2. PDF Logic
            elif 'application/pdf' in media_type:
                pdf_path = DOCS_DIR / f"doc_{sender_id[-4:]}.pdf"
                with open(pdf_path, "wb") as f:
                    f.write(media_data)
                
                reader = PdfReader(pdf_path)
                text_content = "\n".join([p.extract_text() for p in reader.pages])
                pdf_context[sender_id] = text_content
                resp.message(f"✅ PDF Processed ({len(reader.pages)} pages). You may now query its content.")

            # 3. Audio Logic (Multilingual)
            elif 'audio' in media_type:
                audio_prompt = "Listen to this audio and reply in the EXACT SAME LANGUAGE and TONE."
                audio_res = model.generate_content([audio_prompt, {"mime_type": media_type, "data": media_data}])
                reply_text = audio_res.text

                # Generate TTS
                tts = gTTS(text=reply_text.replace('*', ''), lang='hi') 
                audio_filename = f"reply_{datetime.now().strftime('%H%M%S')}.mp3"
                tts.save(str(AUDIO_DIR / audio_filename))
                
                resp.message(f"🗣️ {reply_text}")
                resp.message("").media(f"{host_url}audios/{audio_filename}")

        # === TEXT HANDLING ===
        else:
            # 1. Handle Pending Image Tagging
            if sender_id in pending_image_context and photos_collection is not None:
                ctx = pending_image_context[sender_id]
                clean_name = extract_clean_name(msg_body) # Smart Extraction
                
                doc = {
                    "user_id": sender_id,
                    "description": ctx['desc'],
                    "name_tag": clean_name,
                    "timestamp": datetime.now()
                }
                photos_collection.insert_one(doc)
                del pending_image_context[sender_id]
                resp.message(f"✅ Memory Saved: Tagged as '{clean_name}'.")

            # 2. Handle PDF Q&A
            elif sender_id in pdf_context:
                rag_prompt = f"Context: {pdf_context[sender_id]}\n\nUser Question: {msg_body}"
                res = model.generate_content(rag_prompt)
                resp.message(res.text)

            # 3. General Chat + Search + Memory Retrieval
            else:
                # Fetch recent memories for context
                mem_str = ""
                # FIX: Explicit check for None
                if photos_collection is not None:
                    recent = photos_collection.find({"user_id": sender_id}).sort("timestamp", -1).limit(3)
                    mem_str = ", ".join([f"{r['name_tag']} ({r['description']})" for r in recent])

                # Check for Search Intent
                web_data = ""
                search_triggers = ["news", "price", "weather", "who is", "what is", "latest", "current"]
                if any(t in msg_body.lower() for t in search_triggers) or "?" in msg_body:
                    search_res = search_internet(msg_body)
                    if search_res:
                        web_data = f"Web Search Results:\n{search_res}"

                # Final Response Generation
                final_prompt = f"""
                User Memories: {mem_str}
                External Info: {web_data}
                User Input: {msg_body}
                
                Instructions: Answer naturally. Use External Info if present. Do not hallucinate.
                """
                res = model.generate_content(final_prompt)
                resp.message(res.text)

    except Exception as e:
        print(f"Runtime Error: {e}")
        resp.message("⚠️ System currently unavailable. Please try again later.")

    return Response(content=str(resp), media_type="application/xml")