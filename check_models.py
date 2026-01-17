import google.generativeai as genai
import os
from dotenv import load_dotenv

# Load Environment Variables
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")

if not api_key:
    print("❌ Error: GOOGLE_API_KEY nahi mila! .env file check karein.")
else:
    print(f"✅ API Key Found: {api_key[:5]}...*****")
    
    try:
        genai.configure(api_key=api_key)
        print("\n🔍 Checking Available Models via Google API...")
        
        # List all models
        count = 0
        for m in genai.list_models():
            # Sirf wo models dikhao jo Chat/Content Generation karte hain
            if 'generateContent' in m.supported_generation_methods:
                print(f" - {m.name}")
                count += 1
        
        if count == 0:
            print("⚠️ Koi model nahi mila. Shayad API Key mein permission issue hai.")
            
    except Exception as e:
        print(f"\n❌ CRITICAL ERROR: {e}")
        print("Tip: Agar error 'genai has no attribute' hai, toh library update karein.")