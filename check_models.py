import google.generativeai as genai
import os
from dotenv import load_dotenv

# API Key load karein
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")

if not api_key:
    print("❌ Error: API Key nahi mili! .env file check karein.")
else:
    genai.configure(api_key=api_key)
    print("🔍 Checking available models...\n")
    
    try:
        for m in genai.list_models():
            # Sirf wahi models dikhao jo content generate kar sakte hain
            if 'generateContent' in m.supported_generation_methods:
                print(f"✅ {m.name}")
    except Exception as e:
        print(f"❌ Error: {e}")