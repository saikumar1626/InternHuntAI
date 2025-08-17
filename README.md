# 🤖 InternHuntAI

InternHuntAI is an **AI-powered LinkedIn automation bot** that helps students and job seekers apply to internships with one click.  
It uses **Python, Playwright, Flask, and LocalXpose/Ngrok** to auto-fill applications, handle resumes, and give mobile access via a secure tunnel.

---

## 🎯 Features
- 🔐 Secure LinkedIn login with Playwright
- 🛡️ Captcha detection with manual assist
- 📄 Auto-uploads role-specific resumes
- 📱 Flask dashboard (accessible on phone via tunnel)
- 🌐 Remote access using LocalXpose
- 📝 Application log saved for tracking

---

## 📂 Demo Recordings & Screenshots
All project **screenshots** and **step-by-step recordings** are available here:  
👉 [View Demo Folder on Google Drive](https://drive.google.com/drive/folders/1uZz5E7LqTMuuY8eIvdLBIqcLQ8W4ld9c?usp=drive_link)

This folder contains:
- 🎥 **Start-to-End Screen Recordings** of setup & usage  
- 📸 **Screenshots of Terminal & Mobile Access**  
- 🖥️ **Final working demo of InternHuntAI**  

---

## ⚙️ Tech Stack
- **Python** 🐍
- **Playwright** (Browser automation)
- **Flask** (Web dashboard API)
- **LocalXpose / Ngrok** (Remote tunneling)
- **YAML + JSON** (Configuration & logs)

---

## 🚀 How to Run
```bash
# 1️⃣ Clone the repo
git clone https://github.com/saikumar1626/InternHuntAI
cd InternHuntAI

# 2️⃣ Create virtual environment
python -m venv venv
venv\Scripts\activate    # On Windows

# 3️⃣ Install dependencies
pip install -r requirements.txt

# 4️⃣ Configure your LinkedIn login in `agent_config.yaml`

# 5️⃣ Start the Flask server
python server.py

# 6️⃣ Expose it online (using LocalXpose)
cd C:\localxpose
.\loclx.exe tunnel http 5000
