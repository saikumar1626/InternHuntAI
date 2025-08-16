import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
import yaml

# Load config
with open("agent_config.yaml", "r") as f:
    config = yaml.safe_load(f)

EMAIL = config["login"]["email"]
PASSWORD = config["login"]["password"]
KEYWORDS = config["job_search"]["keywords"]
LOCATION = config["job_search"]["location"]

def linkedin_login(driver):
    driver.get("https://www.linkedin.com/login")
    time.sleep(2)
    driver.find_element(By.ID, "username").send_keys(EMAIL)
    driver.find_element(By.ID, "password").send_keys(PASSWORD)
    driver.find_element(By.XPATH, "//button[@type='submit']").click()
    time.sleep(3)

def fetch_jobs(driver):
    jobs = []
    for keyword in KEYWORDS:
        search_url = f"https://www.linkedin.com/jobs/search/?keywords={keyword}&location={LOCATION}&f_TP=1"
        driver.get(search_url)
        time.sleep(3)
        listings = driver.find_elements(By.CLASS_NAME, "base-card")
        for listing in listings:
            try:
                title = listing.find_element(By.CLASS_NAME, "base-search-card__title").text.strip()
                company = listing.find_element(By.CLASS_NAME, "base-search-card__subtitle").text.strip()
                link = listing.find_element(By.TAG_NAME, "a").get_attribute("href")
                jobs.append({"title": title, "company": company, "link": link})
            except:
                pass
    return jobs

def main():
    chrome_options = Options()
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    driver = webdriver.Chrome(options=chrome_options)

    linkedin_login(driver)
    jobs = fetch_jobs(driver)
    driver.quit()

    print(f"✅ Found {len(jobs)} internships!")
    for job in jobs:
        print(f"{job['title']} at {job['company']} -> {job['link']}")

if __name__ == "__main__":
    main()
