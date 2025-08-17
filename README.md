# InternHuntAI 🚀

InternHuntAI is an AI-powered LinkedIn automation bot that:
- Logs into LinkedIn securely
- Detects & bypasses captchas with manual assist
- Auto-applies to internships using tailored resumes
- Tracks applied jobs in a JSON log
- Exposes a local Flask web interface (via LocalXpose/ngrok) to control from phone

## 🔧 Tech Stack
- Python 3
- Playwright (browser automation)
- Flask (web server)
- YAML for configs
- LocalXpose/Ngrok for remote access

## 🚀 Getting Started
```bash
git clone https://github.com/YOUR_USERNAME/InternHuntAI.git
cd InternHuntAI
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
