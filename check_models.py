import google.generativeai as genai
import os
from dotenv import load_dotenv
from pathlib import Path

# Setup
BASE_DIR = Path(__file__).resolve().parent
env_file = BASE_DIR / ".env"
load_dotenv(dotenv_path=env_file)
api_key = os.getenv("GOOGLE_API_KEY")

if not api_key:
    print("❌ API Key nahi mili!")
else:
    genai.configure(api_key=api_key)
    print("🔍 Searching for available models...\n")
    
    try:
        for m in genai.list_models():
            # Sirf wahi models dikhao jo content generate kar sakein
            if 'generateContent' in m.supported_generation_methods:
                print(f"✅ Available: {m.name}")
    except Exception as e:
        print(f"❌ Error: {e}")