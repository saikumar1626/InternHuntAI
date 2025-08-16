import time
import os
import json
import yaml
from loguru import logger
from playwright.sync_api import sync_playwright

# === LOAD CONFIG ===
with open("agent_config.yaml", "r") as f:
    cfg = yaml.safe_load(f)

EMAIL = cfg["login"]["email"]
PASSWORD = cfg["login"]["password"]
PHONE = cfg["application"]["phone_number"]
RESUME_DS = os.path.abspath(cfg["application"]["resumes"]["data_science"])
RESUME_SE = os.path.abspath(cfg["application"]["resumes"]["software_engineering"])

# === CAPTCHA DETECTION (wait forever + flashing) ===
def detect_captcha(page):
    if "captcha" in page.content().lower():
        logger.warning("CAPTCHA detected! Please solve it manually in the opened browser.")
    while "captcha" in page.content().lower():
        try:
            page.evaluate("""
                document.body.style.backgroundColor = 
                    document.body.style.backgroundColor === 'red' ? '' : 'red';
            """)
        except:
            pass
        time.sleep(2)
    logger.info("CAPTCHA cleared, resuming automation...")

# === LOGIN TO LINKEDIN ===
def linkedin_login(page):
    logger.info("Navigating to LinkedIn login page...")
    page.goto("https://www.linkedin.com/login", timeout=0)
    page.wait_for_selector("input#username", timeout=0)
    page.fill("input#username", EMAIL)
    page.fill("input#password", PASSWORD)
    page.click("button[type='submit']")
    detect_captcha(page)
    page.wait_for_load_state("domcontentloaded")
    logger.info("Login successful.")

# === DETERMINE WHICH RESUME TO USE ===
def select_resume(job_title):
    job_title_lower = job_title.lower()
    if "data" in job_title_lower or "ml" in job_title_lower or "ai" in job_title_lower:
        return RESUME_DS
    return RESUME_SE

# === APPLY TO JOB ===
def apply_to_job(page, job_url, job_title):
    page.goto(job_url, timeout=0)
    detect_captcha(page)

    try:
        page.click("button.jobs-apply-button", timeout=5000)
    except:
        logger.info(f"No Easy Apply button for: {job_title}")
        return False

    # Fill phone
    try:
        phone_input = page.query_selector("input[aria-label='Phone number']")
        if phone_input:
            phone_input.fill(PHONE)
    except:
        pass

    # Upload resume
    resume_path = select_resume(job_title)
    try:
        upload_input = page.query_selector("input[type='file']")
        if upload_input:
            upload_input.set_input_files(resume_path)
            logger.info(f"Uploaded resume: {resume_path}")
    except:
        pass

    # Submit if allowed
    try:
        submit_btn = page.query_selector("button[aria-label='Submit application']")
        if submit_btn:
            submit_btn.click()
            logger.success(f"Applied to {job_title}")
            return True
    except:
        logger.warning(f"Could not submit for: {job_title}")

    return False

# === SAFE LOG LOADING ===
def load_applied_log():
    applied_log_path = "applied_log.json"
    if not os.path.exists(applied_log_path):
        return [], applied_log_path
    try:
        with open(applied_log_path, "r") as f:
            data = json.load(f)
            if not isinstance(data, list):
                raise ValueError("Invalid log format")
            return data, applied_log_path
    except (json.JSONDecodeError, ValueError):
        logger.warning("applied_log.json is empty or corrupted — resetting.")
        return [], applied_log_path

# === MAIN ===
def main():
    applied_log, applied_log_path = load_applied_log()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        linkedin_login(page)

        # Example search (Easy Apply, India, Internship)
        page.goto("https://www.linkedin.com/jobs/search/?keywords=Internship&location=India&f_AL=true&f_E=1", timeout=0)
        page.wait_for_load_state("domcontentloaded")

        job_cards = page.query_selector_all(".jobs-search-results__list-item")
        logger.info(f"Found {len(job_cards)} jobs.")

        for card in job_cards:
            job_title = card.query_selector("a.job-card-list__title").inner_text().strip()
            job_url = card.query_selector("a.job-card-list__title").get_attribute("href")
            if any(j["url"] == job_url for j in applied_log):
                logger.info(f"Already applied: {job_title}")
                continue

            if apply_to_job(page, job_url, job_title):
                applied_log.append({"title": job_title, "url": job_url})
                with open(applied_log_path, "w") as f:
                    json.dump(applied_log, f, indent=2)

        logger.info("All done.")
        browser.close()

if __name__ == "__main__":
    main()
