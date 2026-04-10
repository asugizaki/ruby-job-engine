# ELITE RUBY JOB INTELLIGENCE SYSTEM (FINAL PRODUCTION)
# ----------------------------------------------------

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

# roles we WANT
ENGINEER_KEYWORDS = [
    "engineer", "developer", "backend", "full stack",
    "software engineer", "software developer"
]

# roles we DON'T want
EXCLUDE_KEYWORDS = [
    "staff", "principal", "lead", "manager", "director", "vp", "head", "architect"
]

# ---------------- HELPERS ----------------

def contains_ruby(text):
    return any(k in text.lower() for k in RUBY_KEYWORDS)

def is_valid_engineer_role(title):
    t = title.lower()

    if any(bad in t for bad in EXCLUDE_KEYWORDS):
        return False

    if any(good in t for good in ENGINEER_KEYWORDS):
        return True

    return False

def is_canada_friendly(text):
    t = text.lower()

    if "canada" in t:
        return True

    if "remote" in t and not any(x in t for x in ["india", "philippines", "latam", "europe only"]):
        return True

    return False

def extract_salary(text):
    if not text:
        return None

    patterns = [
        r"\$\d{2,3}[,]?\d{3}\s*[–-]\s*\$\d{2,3}[,]?\d{3}",
        r"\$\d{2,3}[kK]\s*[–-]\s*\$\d{2,3}[kK]",
        r"\$\d{2,3}[,]?\d{3}"
    ]

    for p in patterns:
        match = re.search(p, text)
        if match:
            return match.group(0)

    return None

# ---------------- COMPANY STORAGE ----------------

def load_companies():
    try:
        with open(COMPANY_STORE_FILE, "r") as f:
            data = json.load(f)
            log("STATE", f"Loaded {sum(len(v) for v in data.values())} companies")
            return data
    except:
        return {"greenhouse": [], "lever": [], "ashby": [], "workable": []}

def save_companies(data):
    with open(COMPANY_STORE_FILE, "w") as f:
        json.dump(data, f, indent=2)
    log("STATE", f"Saved {sum(len(v) for v in data.values())} companies")

def detect_platform(url):
    if "greenhouse" in url:
        return "greenhouse"
    if "lever" in url:
        return "lever"
    if "ashby" in url:
        return "ashby"
    if "workable" in url:
        return "workable"
    return None

def extract_slug(url):
    parts = url.split("/")
    for p in parts:
        if p and p not in ["jobs", "job", "boards"]:
            return p
    return None

def discover_company(url, store):
    platform = detect_platform(url)
    if not platform:
        return

    slug = extract_slug(url)
    if not slug:
        return

    if slug not in store[platform]:
        store[platform].append(slug)
        log("DISCOVERY", f"{platform}: {slug}")

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
    log("HIMALAYAS", "Fetching Canada Ruby jobs")

    url = "https://himalayas.app/jobs/countries/canada?q=ruby"
    html = await fetch(session, url)

    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    links = []

    for a in soup.find_all("a", href=True):
        href = a["href"]

        if "/jobs/" in href:
            full = urljoin(url, href)
            links.append(full)

    links = list(set(links))
    log("HIMALAYAS", f"Found {len(links)} links")

    jobs = []
    tasks = [process_job(session, u) for u in links[:30]]
    results = await asyncio.gather(*tasks)

    for r in results:
        if r:
            jobs.append(r)

    log("HIMALAYAS", f"Valid jobs: {len(jobs)}")
    return jobs

# ---------------- JOB PROCESSING ----------------

async def process_job(session, url):
    html = await fetch(session, url)
    if not html:
        log("REJECT", f"[NO_HTML] {url}")
        return None

    soup = BeautifulSoup(html, "html.parser")

    title = soup.find("h1")
    title = title.get_text(strip=True) if title else "Unknown"

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

    store = load_companies()
    jobs = []

    async with aiohttp.ClientSession() as session:
        jobs += await fetch_himalayas(session)

    # DISCOVER COMPANIES
    for j in jobs:
        discover_company(j["link"], store)

    save_companies(store)

    # GROUP BY COMPANY
    grouped = {}
    for j in jobs:
        grouped.setdefault(j["company"], []).append(j)

    # EMAIL FORMAT
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
