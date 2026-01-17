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

# Load environment variables
load_dotenv()

# Configuration
API_KEY = os.getenv("GOOGLE_API_KEY")
MONGO_URI = os.getenv("MONGO_URI")

if not API_KEY:
    print("Error: GOOGLE_API_KEY is missing.")

# Initialize Gemini Model with System Identity
SYSTEM_PROMPT = """
You are 'ThirdEye', an intelligent AI Assistant created by Rakesh Raushan.
Your purpose is to help users with daily tasks, academic questions, and visual understanding.

IDENTITY & CORE BEHAVIOR:
1. CREATOR: You were built by Rakesh Raushan using Python, FastAPI, and Gemini Vision.
2. PURPOSE: You are a General AI Assistant. However, this specific version acts as a showcase project for 'Puch AI'.
3. TRUTH & FACTS: Prioritize truth. Use 'Internet Search Data' for unknown topics. Do not hallucinate.

KNOWLEDGE BASE:
- Puch AI: An AI startup founded by Siddharth Bhatia focused on accessibility.
- General: Use your internal knowledge + Internet Search for history, math, news, etc.

INTERACTION:
- Be helpful and polite.
- Always describe an image first before asking to save it.
"""

genai.configure(api_key=API_KEY)
# Hum 'gemini-2.0-flash' use karenge jo aapki list mein hai
model = genai.GenerativeModel('gemini-2.0-flash', system_instruction=SYSTEM_PROMPT)

app = FastAPI()

# Database Connection (MongoDB Atlas)
photos_collection = None
if MONGO_URI:
    try:
        # Fix: TLS configuration required for secure connection on Render
        client = MongoClient(MONGO_URI, tls=True, tlsAllowInvalidCertificates=True)
        db = client.thirdeye_db
        photos_collection = db.photos
        client.admin.command('ping')
        print("Connected to MongoDB.")
    except Exception as e:
        print(f"MongoDB Connection Error: {e}")

# File Storage Setup
BASE_DIR = Path("/tmp")
AUDIO_DIR = BASE_DIR / "audios"
DOCS_DIR = BASE_DIR / "documents"

for folder in [AUDIO_DIR, DOCS_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

app.mount("/audios", StaticFiles(directory=str(AUDIO_DIR)), name="audios")

# In-memory state management
temp_image_state = {}  # Holds image description before saving
pdf_context = {}       # Holds parsed PDF text

# --- Utility Functions ---

def clean_text_for_audio(text):
    """Removes special characters for cleaner TTS output."""
    return text.replace('*', '').replace('#', '').replace('_', '').replace('-', ' ')

def get_media_content(url):
    """Fetches media binary data from Twilio URL."""
    return requests.get(url).content

def google_search(query):
    """Performs a real-time internet search using DuckDuckGo."""
    try:
        print(f"Searching: {query}")
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
            if results:
                return "\n".join([f"- {r['body']}" for r in results])
    except Exception as e:
        print(f"Search failed: {e}")
    return None

# --- Routes ---

@app.head("/")
async def keep_alive():
    return Response(status_code=200)

@app.get("/")
async def root():
    return {"status": "ThirdEye System Online"}

@app.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    form = await request.form()
    num_media = int(form.get('NumMedia', 0))
    msg_body = form.get('Body', '').strip()
    sender_id = form.get('From')
    
    # Construct host URL for serving static files
    host_url = str(request.base_url).replace("http://", "https://")
    resp = MessagingResponse()

    try:
        # Handle Media Messages (Images, PDFs, Audio)
        if num_media > 0:
            media_url = form.get('MediaUrl0')
            content_type = form.get('MediaContentType0')
            
            # Case 1: Image Handling
            if 'image' in content_type:
                img_data = get_media_content(media_url)
                
                # Analyze image with Gemini Vision
                image_part = {"mime_type": content_type, "data": img_data}
                prompt = "Describe this image in 1 short sentence (Hinglish/English mix). What main object is this?"
                ai_response = model.generate_content([prompt, image_part])
                description = ai_response.text.strip()

                # Store state temporarily to wait for user's tag
                temp_image_state[sender_id] = {
                    "desc": description,
                    "img_data": img_data 
                }
                
                resp.message(f"👁️ *Maine Dekha:* {description}\n\nKya aap isey yaad rakhna chahte hain? Agar haan, to iska *Naam* likh kar bhejein.")

            # Case 2: PDF Handling
            elif 'application/pdf' in content_type:
                pdf_data = get_media_content(media_url)
                pdf_path = DOCS_DIR / f"doc_{sender_id[-4:]}.pdf"
                with open(pdf_path, "wb") as f:
                    f.write(pdf_data)
                
                # Extract text for RAG
                reader = PdfReader(pdf_path)
                text_content = ""
                for page in reader.pages:
                    text_content += page.extract_text() + "\n"
                
                pdf_context[sender_id] = text_content
                resp.message(f"✅ PDF Processed ({len(reader.pages)} pages). You can now ask questions about it.")

            # Case 3: Audio Handling
            elif 'audio' in content_type:
                audio_data = get_media_content(media_url)
                audio_part = {"mime_type": content_type, "data": audio_data}
                
                prompt = "Listen to this audio and reply in the EXACT SAME LANGUAGE and TONE."
                ai_response = model.generate_content([prompt, audio_part])
                bot_text = ai_response.text
                
                resp.message(f"🗣️ {bot_text}")
                
                # Convert reply to Audio (TTS)
                tts = gTTS(text=clean_text_for_audio(bot_text), lang='hi')
                audio_fn = f"reply_{datetime.now().strftime('%H%M%S')}.mp3"
                tts.save(str(AUDIO_DIR / audio_fn))
                msg = resp.message("")
                msg.media(f"{host_url}audios/{audio_fn}")

        # Handle Text Messages
        else:
            # 1. Save Image Tag (If awaiting user input)
            if sender_id in temp_image_state and photos_collection is not None:
                description = temp_image_state[sender_id]["desc"]
                
                photo_doc = {
                    "user_id": sender_id, 
                    "description": description, 
                    "name_tag": msg_body,
                    "timestamp": datetime.now()
                }
                photos_collection.insert_one(photo_doc)
                del temp_image_state[sender_id]
                resp.message(f"✅ Saved: Photo tagged as '{msg_body}'.")

            # 2. RAG Response (If PDF context exists)
            elif sender_id in pdf_context:
                prompt = f"Based on this PDF Content: {pdf_context[sender_id]}\n\nUser Question: {msg_body}"
                ai_response = model.generate_content(prompt)
                resp.message(ai_response.text)

            # 3. Standard Chat + Search + Memory
            else:
                # Retrieve Long-term Memory
                memory_text = ""
                if photos_collection is not None:
                    recent = photos_collection.find({"user_id": sender_id}).sort("timestamp", -1).limit(3)
                    mems = [f"{r['name_tag']} ({r['description']})" for r in recent]
                    if mems: memory_text = "User's Memories: " + ", ".join(mems)

                # Check for Search Intent
                search_result = ""
                triggers = ["who", "what", "where", "when", "how", "define", "explain", 
                           "news", "price", "weather", "score", "latest", "current", 
                           "meaning", "example", "history", "vs", "difference"]
                
                if any(word in msg_body.lower() for word in triggers) or "?" in msg_body:
                    search_data = google_search(msg_body)
                    if search_data:
                        search_result = f"\nVERIFIED INTERNET DATA:\n{search_data}\n"

                # Generate Final Response
                final_prompt = f"""
                {memory_text}
                {search_result}
                
                User Message: {msg_body}
                
                INSTRUCTION:
                1. You are ThirdEye.
                2. If asking about 'Puch AI', define it as a startup by Siddharth Bhatia.
                3. For general queries, use 'VERIFIED INTERNET DATA'.
                4. No hallucinations.
                """
                ai_response = model.generate_content(final_prompt)
                resp.message(ai_response.text)

    except Exception as e:
        print(f"Runtime Error: {e}")
        resp.message("⚠️ System Error: Please try again later.")

    return Response(content=str(resp), media_type="application/xml")