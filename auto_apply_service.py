"""
auto_apply_service.py

Continuous LinkedIn monitor + auto-apply service.

Controls:
  - Flask endpoints: /start, /stop, /status (Bearer token from config)
Behavior:
  - Runs a background worker that polls LinkedIn every refresh_interval_minutes
  - Auto-applies to Easy Apply internship postings matching keywords & location
  - Sends Pushbullet notifications for non-Easy-Apply postings (if configured)
  - CAPTCHA-aware: flashes page and waits until manual solve
  - Persists applied jobs in applied_log.json to avoid duplicates
"""

import os
import time
import json
import yaml
import threading
import secrets
from datetime import datetime
from functools import wraps

import requests
from loguru import logger
from flask import Flask, request, jsonify

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# -------------------------
# Config & constants
# -------------------------
CONFIG_PATH = "agent_config.yaml"
APPLIED_LOG_PATH = "applied_log.json"
STORAGE_STATE = "playwright_storage.json"

if not os.path.exists(CONFIG_PATH):
    raise FileNotFoundError(f"{CONFIG_PATH} missing — create it first and fill credentials.")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

# safe lookups
LOGIN = cfg.get("login", {})
JOB_SEARCH = cfg.get("job_search", {})
APP_CFG = cfg.get("application", {})
NOTIF_CFG = cfg.get("notifications", {})
SERVER_CFG = cfg.get("server", {})
RUNTIME_CFG = cfg.get("runtime", {})

EMAIL = LOGIN.get("email")
PASSWORD = LOGIN.get("password")
KEYWORDS = JOB_SEARCH.get("keywords", ["Internship"])
LOCATION = JOB_SEARCH.get("location", "India")
REMOTE_ONLY = JOB_SEARCH.get("remote_only", True)

PHONE = APP_CFG.get("phone_number", "")
RESUME_DS = os.path.abspath(APP_CFG.get("resumes", {}).get("data_science", ""))
RESUME_SE = os.path.abspath(APP_CFG.get("resumes", {}).get("software_engineering", ""))

PUSHBULLET_KEY = NOTIF_CFG.get("pushbullet_api_key", "")
NOTIFY_NORMAL = NOTIF_CFG.get("notify_for_normal_apply", True)

REFRESH_MIN = int(RUNTIME_CFG.get("refresh_interval_minutes", 5))

API_HOST = SERVER_CFG.get("host", "0.0.0.0")
API_PORT = int(SERVER_CFG.get("port", 5000))
API_TOKEN = SERVER_CFG.get("api_token") or ""

if not API_TOKEN:
    API_TOKEN = secrets.token_urlsafe(24)
    cfg.setdefault("server", {})["api_token"] = API_TOKEN
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f)
    logger.info(f"Generated API token and saved to config (keep it secret).")

# -------------------------
# HTTP (Flask) server for control
# -------------------------
app = Flask("auto_apply_service")

def require_token(f):
    @wraps(f)
    def wrapper(*a, **kw):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error":"missing token"}), 401
        token = auth.split(" ",1)[1]
        if token != API_TOKEN:
            return jsonify({"error":"invalid token"}), 403
        return f(*a, **kw)
    return wrapper

# worker state
worker_thread = None
worker_stop_event = threading.Event()
worker_lock = threading.Lock()

# -------------------------
# Helpers: logs, persistence
# -------------------------
def now_iso():
    return datetime.utcnow().isoformat()

def safe_load_applied_log():
    if not os.path.exists(APPLIED_LOG_PATH):
        return []
    try:
        with open(APPLIED_LOG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        logger.warning("applied_log.json is missing/corrupted — resetting")
        return []

def safe_save_applied_log(data):
    with open(APPLIED_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# -------------------------
# Pushbullet notification
# -------------------------
def send_pushbullet_notification(title, body, link=None):
    if not PUSHBULLET_KEY:
        logger.info("Pushbullet API key not configured — skipping notification.")
        return False
    headers = {"Access-Token": PUSHBULLET_KEY, "Content-Type": "application/json"}
    payload = {"type": "note", "title": title, "body": body}
    if link:
        payload["type"] = "link"
        payload["title"] = title
        payload["url"] = link
        payload["body"] = body
    try:
        r = requests.post("https://api.pushbullet.com/v2/pushes", headers=headers, json=payload, timeout=10)
        if r.status_code == 200:
            logger.info("Pushbullet: notification sent")
            return True
        else:
            logger.warning(f"Pushbullet failed: {r.status_code} {r.text}")
            return False
    except Exception as e:
        logger.error(f"Pushbullet error: {e}")
        return False

# -------------------------
# Playwright helpers & captcha handling
# -------------------------
def detect_captcha_and_wait(page):
    """If a captcha is present, flash page background and wait until it's gone."""
    try:
        content = page.content().lower()
    except Exception:
        content = ""
    if "captcha" not in content and not page.query_selector("iframe[src*='recaptcha']"):
        return
    logger.warning("CAPTCHA detected — please solve it in the opened browser. Waiting until cleared...")
    while True:
        try:
            content = page.content().lower()
        except Exception:
            content = ""
        if "captcha" not in content and not page.query_selector("iframe[src*='recaptcha']"):
            logger.info("CAPTCHA cleared — resuming.")
            # restore background
            try:
                page.evaluate("document.body.style.backgroundColor = ''")
            except Exception:
                pass
            return
        # flash background
        try:
            page.evaluate("""
                document.body.style.backgroundColor = 
                    document.body.style.backgroundColor === 'red' ? 'white' : 'red';
            """)
        except Exception:
            pass
        time.sleep(2)

def choose_resume_for_title(title):
    t = (title or "").lower()
    if any(k in t for k in ("data", "machine", "ml", "ai", "analyst", "scientist")):
        return RESUME_DS
    return RESUME_SE

# -------------------------
# Job operations: search + apply
# -------------------------
def scrape_jobs_on_search_page(page, keyword, location, remote_only, easy_apply_only):
    """Navigate to a search URL and return list of job dicts {title, company, url, easy_apply_flag}."""
    kw_q = keyword.replace(" ", "%20")
    loc_q = location.replace(" ", "%20")
    # Build LinkedIn URL: f_E=1 -> internship / f_AL=true -> Easy Apply, f_WT=2 -> remote
    url = f"https://www.linkedin.com/jobs/search/?keywords={kw_q}&location={loc_q}&f_E=1"
    if easy_apply_only:
        url += "&f_AL=true"
    if remote_only:
        url += "&f_WT=2"
    logger.info(f"Searching LinkedIn: {keyword} @ {location} (easy_apply={easy_apply_only})")
    page.goto(url, timeout=0)
    time.sleep(1.2)
    # scroll to load cards
    for _ in range(8):
        try:
            page.mouse.wheel(0, 800)
        except Exception:
            pass
        time.sleep(0.4)
    cards = page.query_selector_all("ul.jobs-search__results-list li")
    results = []
    for c in cards:
        try:
            title_el = c.query_selector("h3")
            comp_el = c.query_selector("h4")
            link_el = c.query_selector("a.job-card-list__title")
            if not link_el:
                link_el = c.query_selector("a")
            title = title_el.inner_text().strip() if title_el else ""
            company = comp_el.inner_text().strip() if comp_el else ""
            href = link_el.get_attribute("href") if link_el else None
            if href:
                # canonical url
                href = href.split("?")[0]
                results.append({
                    "title": title,
                    "company": company,
                    "url": href,
                    "easy_apply": easy_apply_only
                })
        except Exception:
            continue
    # dedupe
    uniq = []
    seen = set()
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            uniq.append(r)
    return uniq

def apply_easy_apply(page, job, cfg):
    """Attempt Easy Apply. Returns dict(status, notes)."""
    try:
        page.goto(job["url"], timeout=0)
        time.sleep(1.0)
        detect_captcha_and_wait(page)
        # find Easy Apply button
        btn = None
        for sel in ["button.jobs-apply-button", "xpath=//button[contains(., 'Easy Apply')]", "button[data-control-name='apply_open']"]:
            try:
                b = page.query_selector(sel)
                if b:
                    btn = b
                    break
            except Exception:
                pass
        if not btn:
            return {"status":"no_easy_apply", "notes":"no Easy Apply"}
        btn.click()
        time.sleep(1.0)
        detect_captcha_and_wait(page)
        # estimate steps
        modal = page.query_selector("div.jobs-easy-apply-modal, div.jobs-modal__content, div.jobs-easy-apply-form")
        step_est = 1
        if modal:
            next_cands = modal.query_selector_all("button, input[type='button']")
            cnt = 0
            for b in next_cands:
                try:
                    txt = (b.inner_text() or b.get_attribute("value") or "").lower()
                    if any(k in txt for k in ("next","continue","review","forward")):
                        cnt += 1
                except Exception:
                    pass
            step_est = max(1, cnt+1)
        if APP_CFG.get("skip_long_forms", True) and step_est > 2:
            # close modal
            try:
                cb = page.query_selector("button[aria-label='Dismiss'], button[aria-label='Close']")
                if cb: cb.click()
                else: page.keyboard.press("Escape")
            except Exception:
                pass
            return {"status":"skipped_long_form", "notes":f"{step_est} steps"}
        # fill phone
        phone = APP_CFG.get("phone_number","")
        if phone:
            for sel in ["input[name*='phone']", "input[placeholder*='Phone']", "input[aria-label*='phone']"]:
                try:
                    el = page.query_selector(sel)
                    if el:
                        el.fill(str(phone))
                except Exception:
                    pass
        # upload resume
        resume = choose_resume_for_title(job.get("title",""))
        try:
            file_input = page.query_selector("input[type='file']")
            if file_input and os.path.exists(resume):
                file_input.set_input_files(resume)
                time.sleep(0.6)
        except Exception:
            pass
        # click submit flow
        def click_by_text(words):
            for w in words:
                try:
                    el = page.query_selector(f"xpath=//button[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), '{w}')]")
                    if el:
                        el.click()
                        return True
                except Exception:
                    pass
            return False
        submitted = False
        if click_by_text(["submit application","submit","apply now","send","finish"]):
            submitted = True
            time.sleep(1.0)
        else:
            for _ in range(2):
                if click_by_text(["next","continue","review"]):
                    time.sleep(0.9)
                    detect_captcha_and_wait(page)
                else:
                    break
            if click_by_text(["submit application","submit","apply now","send","finish"]):
                submitted = True
                time.sleep(1.0)
        if submitted and APP_CFG.get("submit_without_review", True):
            return {"status":"applied","notes":"submitted"}
        elif submitted:
            # prepared but not auto-submitted
            return {"status":"prepared","notes":"filled but auto-submit disabled"}
        else:
            return {"status":"filled_no_submit","notes":"could not find final submit"}
    except Exception as e:
        return {"status":"error","notes":str(e)}

# -------------------------
# Worker loop
# -------------------------
def worker_loop():
    logger.info("Worker thread started — monitoring LinkedIn.")
    applied = safe_load_applied_log()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        # reuse saved auth if available
        if os.path.exists(STORAGE_STATE):
            context = browser.new_context(storage_state=STORAGE_STATE)
        else:
            context = browser.new_context()
        page = context.new_page()
        # login if needed
        page.goto("https://www.linkedin.com/login", timeout=0)
        time.sleep(1.0)
        # Try to do login only if not already authenticated (detect profile icon)
        try:
            page.goto("https://www.linkedin.com/feed", timeout=0)
            time.sleep(1)
            if "login" in page.url:
                page.goto("https://www.linkedin.com/login", timeout=0)
                page.fill("input#username", EMAIL)
                page.fill("input#password", PASSWORD)
                page.click("button[type='submit']")
                detect_captcha_and_wait(page)
                # save state
                try:
                    context.storage_state(path=STORAGE_STATE)
                except Exception:
                    pass
        except Exception:
            pass

        while not worker_stop_event.is_set():
            # Step A: search EASY APPLY jobs and auto-apply
            for kw in KEYWORDS:
                if worker_stop_event.is_set():
                    break
                jobs = scrape_jobs_on_search_page(page, kw, LOCATION, REMOTE_ONLY, easy_apply_only=True)
                logger.info(f"Found {len(jobs)} Easy Apply jobs for '{kw}'")
                for job in jobs:
                    if any(j.get("url")==job.get("url") for j in applied):
                        continue
                    logger.info(f"Attempting Easy Apply for: {job.get('title')} @ {job.get('company')}")
                    res = apply_easy_apply(page, job, cfg)
                    applied.append({"timestamp": now_iso(), "title": job.get("title"), "company": job.get("company"), "url": job.get("url"), "status": res.get("status"), "notes": res.get("notes")})
                    safe_save_applied_log(applied)
                    if worker_stop_event.is_set():
                        break
                    time.sleep(2)

            # Step B: search NORMAL (non-Easy) jobs and notify user
            for kw in KEYWORDS:
                if worker_stop_event.is_set():
                    break
                jobs_norm = scrape_jobs_on_search_page(page, kw, LOCATION, REMOTE_ONLY, easy_apply_only=False)
                # filter normal apply (those where easy_apply likely false) — jobs_norm includes easy ones when flag false,
                # so check page per job whether Easy Apply exists
                logger.info(f"Found {len(jobs_norm)} total jobs for '{kw}' (normal pass)")
                for job in jobs_norm:
                    if any(j.get("url")==job.get("url") for j in applied):
                        continue
                    # visit job page and check easy apply availability
                    page.goto(job["url"], timeout=0)
                    time.sleep(1.0)
                    detect_captcha_and_wait(page)
                    easy_btn = page.query_selector("button.jobs-apply-button") or page.query_selector("xpath=//button[contains(., 'Easy Apply')]")
                    if easy_btn:
                        # this is actually Easy Apply — will be handled in the easy-apply pass next cycle; skip
                        continue
                    # normal apply — notify user
                    logger.info(f"NOTIFY (normal apply): {job.get('title')} @ {job.get('company')}")
                    if NOTIFY_NORMAL:
                        title = f"Job Alert: {job.get('title')} @ {job.get('company')}"
                        body = f"{job.get('title')} at {job.get('company')} — {LOCATION}\n{job.get('url')}"
                        send_pushbullet_notification(title, body, link=job.get("url"))
                    applied.append({"timestamp": now_iso(), "title": job.get("title"), "company": job.get("company"), "url": job.get("url"), "status": "notified", "notes": "normal_apply"})
                    safe_save_applied_log(applied)
                    if worker_stop_event.is_set():
                        break
                    time.sleep(1)

            # wait until next poll
            logger.info(f"Sleeping for {REFRESH_MIN} minutes before next check...")
            for _ in range(REFRESH_MIN * 60):
                if worker_stop_event.is_set():
                    break
                time.sleep(1)

        # cleanup
        try:
            context.storage_state(path=STORAGE_STATE)
        except Exception:
            pass
        browser.close()
    logger.info("Worker thread stopped.")

# -------------------------
# Flask endpoints
# -------------------------
@app.route("/start", methods=["POST"])
@require_token
def api_start():
    global worker_thread
    with worker_lock:
        if worker_thread and worker_thread.is_alive():
            return jsonify({"status":"already_running"})
        worker_stop_event.clear()
        worker_thread = threading.Thread(target=worker_loop, daemon=True)
        worker_thread.start()
        return jsonify({"status":"started"})

@app.route("/stop", methods=["POST"])
@require_token
def api_stop():
    worker_stop_event.set()
    return jsonify({"status":"stopping"})

@app.route("/status", methods=["GET"])
@require_token
def api_status():
    running = bool(worker_thread and worker_thread.is_alive())
    return jsonify({"running": running, "refresh_min": REFRESH_MIN})

# -------------------------
# Entrypoint
# -------------------------
if __name__ == "__main__":
    # Print token for convenience (stored in config)
    logger.info(f"API token (keep secret): {API_TOKEN}")
    # Start Flask in background thread so CLI remains usable
    flask_thread = threading.Thread(target=lambda: app.run(host=API_HOST, port=API_PORT), daemon=True)
    flask_thread.start()
    logger.info(f"Control server started at http://{API_HOST}:{API_PORT} (use Bearer {API_TOKEN})")
    # Start worker automatically
    worker_stop_event.clear()
    worker_thread = threading.Thread(target=worker_loop, daemon=True)
    worker_thread.start()
    # keep main alive and print simple status
    try:
        while True:
            time.sleep(10)
            # optional: write small heartbeat
    except KeyboardInterrupt:
        logger.info("Shutting down: stopping worker.")
        worker_stop_event.set()
        time.sleep(1)
        logger.info("Exited.")
