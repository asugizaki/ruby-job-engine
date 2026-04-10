# ELITE RUBY JOB INTELLIGENCE SYSTEM (HIMALAYAS + RUBYONREMOTE PLAYWRIGHT)
# -----------------------------------------------------------------------

import asyncio
import aiohttp
import json
import smtplib
import logging
import os
import re
from urllib.parse import urljoin
from email.mime.text import MIMEText
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# ---------------- LOGGING ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

def log(step, msg):
    logging.info(f"[{step}] {msg}")

# ---------------- CONFIG ----------------
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

COMPANY_STORE_FILE = "companies.json"

RUBY_KEYWORDS = ["ruby", "rails"]

ENGINEER_KEYWORDS = [
    "engineer", "developer", "backend", "full stack",
    "software engineer", "software developer"
]

EXCLUDE_KEYWORDS = [
    "staff", "principal", "lead", "manager",
    "director", "vp", "head", "architect"
]

# ---------------- HELPERS ----------------

def contains_ruby(text):
    return any(k in text.lower() for k in RUBY_KEYWORDS)

def is_valid_engineer_role(title):
    t = title.lower()

    if any(bad in t for bad in EXCLUDE_KEYWORDS):
        return False

    return any(good in t for good in ENGINEER_KEYWORDS)

def is_canada_friendly(text):
    t = text.lower()

    if "canada" in t:
        return True

    if "remote" in t and not any(x in t for x in ["india", "philippines", "latam"]):
        return True

    return False

def extract_salary(text):
    if not text:
        return None

    patterns = [
        r"\$\d{2,3}[,]?\d{3}\s*[–—-]\s*\$\d{2,3}[,]?\d{3}",
        r"\$\d{2,3}[kK]\s*[–—-]\s*\$\d{2,3}[kK]",
        r"\$\d{2,3}[,]?\d{3}"
    ]

    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(0)

    return None

# ---------------- COMPANY STORAGE ----------------

def load_companies():
    try:
        with open(COMPANY_STORE_FILE, "r") as f:
            return json.load(f)
    except:
        return {"greenhouse": [], "lever": [], "ashby": [], "workable": []}

def save_companies(data):
    with open(COMPANY_STORE_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ---------------- FETCH ----------------

async def fetch(session, url):
    try:
        async with session.get(url, timeout=20) as r:
            return await r.text()
    except Exception as e:
        log("HTTP", f"{url} -> {e}")
        return None

# ---------------- HIMALAYAS ----------------

async def fetch_himalayas(session):
    log("HIMALAYAS", "Fetching")

    url = "https://himalayas.app/jobs/countries/canada?q=ruby"
    html = await fetch(session, url)

    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    links = []
    for a in soup.find_all("a", href=True):
        if "/jobs/" in a["href"]:
            links.append(urljoin(url, a["href"]))

    links = list(set(links))
    log("HIMALAYAS", f"Found {len(links)} links")

    tasks = [process_job(session, u) for u in links[:30]]
    results = await asyncio.gather(*tasks)

    jobs = [r for r in results if r]
    log("HIMALAYAS", f"Valid jobs: {len(jobs)}")

    return jobs

# ---------------- RUBYONREMOTE (PLAYWRIGHT) ----------------

async def fetch_rubyonremote():
    log("RUBYONREMOTE", "Launching browser")

    jobs = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        url = "https://rubyonremote.com/remote-jobs-in-canada/"
        await page.goto(url, timeout=60000)

        await page.wait_for_timeout(5000)  # wait for Cloudflare + JS

        html = await page.content()
        await browser.close()

    soup = BeautifulSoup(html, "html.parser")

    links = []

    for a in soup.find_all("a", href=True):
        href = a["href"]

        if "/jobs/" in href:
            full = urljoin(url, href)
            links.append(full)

    links = list(set(links))
    log("RUBYONREMOTE", f"Found {len(links)} links")

    async with aiohttp.ClientSession() as session:
        tasks = [process_job(session, u) for u in links[:30]]
        results = await asyncio.gather(*tasks)

    jobs = [r for r in results if r]
    log("RUBYONREMOTE", f"Valid jobs: {len(jobs)}")

    return jobs

# ---------------- JOB PROCESSING ----------------

async def process_job(session, url):
    html = await fetch(session, url)
    if not html:
        log("REJECT", f"[NO_HTML] {url}")
        return None

    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else "Unknown"

    body = soup.get_text(" ", strip=True)

    salary = extract_salary(body)

    log("CHECK", f"{title} | {url}")

    if not contains_ruby(body):
        log("REJECT", f"[NOT_RUBY] {url}")
        return None

    if not is_valid_engineer_role(title):
        log("REJECT", f"[NOT_ENGINEER] {title} | {url}")
        return None

    if not is_canada_friendly(body):
        log("REJECT", f"[NOT_CANADA] {url}")
        return None

    log("ACCEPT", f"{title} | salary={salary}")

    return {
        "title": title,
        "link": url,
        "salary": salary or "N/A",
        "company": url.split("/")[4] if "companies" in url else "unknown"
    }

# ---------------- MAIN ----------------

async def main():
    log("SYSTEM", "Starting Job Engine")

    jobs = []

    async with aiohttp.ClientSession() as session:
        jobs += await fetch_himalayas(session)

    # RubyOnRemote uses Playwright separately
    jobs += await fetch_rubyonremote()

    # GROUP
    grouped = {}
    for j in jobs:
        grouped.setdefault(j["company"], []).append(j)

    body = f"🔥 RUBY JOBS - {datetime.now().strftime('%Y-%m-%d')}\n\n"

    for company, js in grouped.items():
        body += f"=== {company.upper()} ===\n"
        for j in js:
            body += f"{j['title']}\n{j['salary']}\n{j['link']}\n\n"

    msg = MIMEText(body)
    msg["Subject"] = "🔥 Ruby Jobs"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(EMAIL_SENDER, EMAIL_PASSWORD)
            s.send_message(msg)
        log("EMAIL", f"Sent {len(jobs)} jobs")
    except Exception as e:
        log("EMAIL", str(e))

    log("SYSTEM", "Complete")

# ---------------- RUN ----------------

if __name__ == "__main__":
    asyncio.run(main())
