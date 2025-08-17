from flask import Flask, jsonify
import threading
import subprocess

app = Flask(__name__)

# Track bot process
bot_process = None

@app.route("/")
def home():
    return """
    <h2>📌 LinkedIn Job Bot Control</h2>
    <a href='/start'>▶️ Start Bot</a><br><br>
    <a href='/stop'>⏹ Stop Bot</a><br><br>
    <a href='/status'>📊 Check Status</a>
    """

@app.route("/start")
def start():
    global bot_process
    if bot_process is None:
        bot_process = subprocess.Popen(["python", "auto_apply.py"])
        return "✅ Bot started!"
    else:
        return "⚠️ Bot is already running."

@app.route("/stop")
def stop():
    global bot_process
    if bot_process:
        bot_process.terminate()
        bot_process = None
        return "⏹ Bot stopped."
    return "⚠️ Bot is not running."

@app.route("/status")
def status():
    if bot_process:
        return "🟢 Bot is running."
    return "🔴 Bot is stopped."

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
