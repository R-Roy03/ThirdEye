# 👁️ ThirdEye AI - The Immortal WhatsApp Assistant

![Python](https://img.shields.io/badge/Python-3.10-blue?style=for-the-badge&logo=python)
![Gemini](https://img.shields.io/badge/AI-Google%20Gemini-orange?style=for-the-badge&logo=google)
![MongoDB](https://img.shields.io/badge/Database-MongoDB%20Atlas-green?style=for-the-badge&logo=mongodb)
![Status](https://img.shields.io/badge/Status-Live-brightgreen?style=for-the-badge)

ThirdEye is a **Multimodal AI Assistant** integrated into WhatsApp. Unlike basic chatbots, ThirdEye has "Long-Term Memory," acts as a "Vision Expert," searches the "Live Internet," and can talk back in audio.

It solves the problem of stateless AI by using **MongoDB Cloud** to remember user details (names, faces, context) permanently, even after server restarts.

## 🚀 Key Features

### 🧠 1. Immortal Memory (MongoDB)
- The bot remembers who you are.
- Stores user details and context in a cloud database.
- **Example:** If you send a photo of a person and say "This is Rahul," the bot will remember Rahul forever.

### 🌍 2. Live Internet Access
- Connected to the real world using DuckDuckGo Search.
- Can answer queries like "Current Gold Rate," "Bangalore Weather," or "Latest News."

### 👁️ 3. Smart Vision (AI Eyes)
- Send any image, and the bot analyzes it.
- Can describe scenes, read handwritten text, or identify objects.

### 🗣️ 4. Voice & Audio Mode
- Send voice notes -> Bot listens and replies in text.
- Bot can also reply with **Audio (TTS)** in the same language and tone.

### 📄 5. Document Expert (RAG)
- Send a PDF (Resume, Invoice, Book).
- Ask questions like "What is the total amount in this bill?" and get instant answers.

---

## 🛠️ Tech Stack

* **Brain:** Google Gemini Pro & Flash (Generative AI)
* **Backend:** Python (FastAPI)
* **Database:** MongoDB Atlas (Cloud NoSQL)
* **Messaging:** Twilio API (WhatsApp)
* **Hosting:** Render (Cloud Server)
* **Tools:** `gTTS` (Text-to-Speech), `PyPDF` (PDF Parsing), `DuckDuckGo` (Search)

---

## 📸 How It Works

1.  **User sends a message** (Text, Image, or Audio) on WhatsApp.
2.  **Twilio** receives the message and forwards it to the **Render Server**.
3.  **FastAPI** processes the input:
    * Checks **MongoDB** for past memories.
    * If needed, searches the **Internet**.
    * Sends context to **Google Gemini AI**.
4.  **Response** is generated (Text or Audio) and sent back to WhatsApp.

---

## 🔮 Future Scope
* Adding Reminder/Alarm features.
* Google Calendar Integration.
* Multi-user group chat analysis.

---
*Created with ❤️ by Rakesh Raushan*
