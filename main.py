import asyncio
import aiohttp
import json
import os
import re
import smtplib
import logging
from urllib.parse import urljoin, urlparse
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

COMPANY_FILE = "companies.json"

RUBY_KEYWORDS = ["ruby", "rails"]

INCLUDE_ROLES = [
    "engineer", "developer", "backend",
    "full stack", "software engineer", "software developer"
]

EXCLUDE_ROLES = [
    "staff", "principal", "director", "vp", "head", "architect", "manager"
]

# ---------------- UTIL ----------------

def load_companies():
    try:
        with open(COMPANY_FILE, "r") as f:
            data = json.load(f)
            log("STATE", f"Loaded companies")
            return data
    except:
        return {"greenhouse": [], "lever": [], "ashby": [], "workable": []}

def save_companies(data):
    with open(COMPANY_FILE, "w") as f:
        json.dump(data, f, indent=2)
    log("STATE", "Saved companies.json")

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

def is_valid_role(title):
    t = title.lower()

    if any(x in t for x in EXCLUDE_ROLES):
        return False

    if any(x in t for x in INCLUDE_ROLES):
        return True

    return False

def is_ruby(text):
    return any(k in text.lower() for k in RUBY_KEYWORDS)

def is_canada_remote(text):
    t = text.lower()
    return "canada" in t or "remote" in t

# ---------------- HTTP ----------------
async def fetch(session, url):
    try:
        async with session.get(url, timeout=25) as r:
            return await r.text()
    except Exception as e:
        log("HTTP", f"{url} -> {e}")
        return None

# ---------------- GREENHOUSE ----------------
async def fetch_greenhouse(session, company):
    url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
    log("GREENHOUSE", company)

    html = await fetch(session, url)
    if not html:
        return []

    try:
        data = json.loads(html)
    except:
        log("GREENHOUSE", f"invalid JSON: {company}")
        return []

    jobs = []
    for j in data.get("jobs", []):
        title = j.get("title", "")
        link = j.get("absolute_url", "")

        if is_ruby(title) and is_valid_role(title):
            jobs.append({
                "title": title,
                "link": link,
                "company": company,
                "salary": "N/A"
            })

    log("GREENHOUSE", f"{company} -> {len(jobs)}")
    return jobs

# ---------------- LEVER ----------------
async def fetch_lever(session, company):
    url = f"https://api.lever.co/v0/postings/{company}"
    log("LEVER", company)

    html = await fetch(session, url)
    if not html:
        return []

    try:
        data = json.loads(html)
    except:
        return []

    jobs = []
    for j in data:
        title = j.get("text", "")
        link = j.get("hostedUrl", "")

        if is_ruby(title) and is_valid_role(title):
            jobs.append({
                "title": title,
                "link": link,
                "company": company,
                "salary": j.get("salary", "N/A")
            })

    log("LEVER", f"{company} -> {len(jobs)}")
    return jobs

# ---------------- ASHBY ----------------
async def fetch_ashby(session, company):
    url = f"https://jobs.ashbyhq.com/api/job-board/{company}"
    log("ASHBY", company)

    html = await fetch(session, url)
    if not html:
        return []

    try:
        data = json.loads(html)
    except:
        return []

    jobs = []
    for j in data.get("jobs", []):
        title = j.get("title", "")
        link = j.get("jobUrl", "")

        if is_ruby(title) and is_valid_role(title):
            jobs.append({
                "title": title,
                "link": link,
                "company": company,
                "salary": "N/A"
            })

    log("ASHBY", f"{company} -> {len(jobs)}")
    return jobs

# ---------------- WORKABLE ----------------
async def fetch_workable(session, company):
    url = f"https://{company}.workable.com/spi/v3/jobs"
    log("WORKABLE", company)

    html = await fetch(session, url)
    if not html:
        return []

    try:
        data = json.loads(html)
    except:
        return []

    jobs = []
    for j in data.get("jobs", []):
        title = j.get("title", "")
        link = j.get("url", "")

        if is_ruby(title) and is_valid_role(title):
            jobs.append({
                "title": title,
                "link": link,
                "company": company,
                "salary": "N/A"
            })

    log("WORKABLE", f"{company} -> {len(jobs)}")
    return jobs

# ---------------- HIMALAYAS ----------------
async def fetch_himalayas(session):
    log("HIMALAYAS", "fetching Canada Ruby search")

    url = "https://himalayas.app/jobs/countries/canada?q=ruby"
    html = await fetch(session, url)

    soup = BeautifulSoup(html, "html.parser")

    links = set()
    for a in soup.find_all("a", href=True):
        if "/jobs/" in a["href"]:
            links.add(urljoin(url, a["href"]))

    log("HIMALAYAS", f"links found: {len(links)}")

    jobs = []

    for link in list(links)[:30]:
        page = await fetch(session, link)
        if not page:
            continue

        soup = BeautifulSoup(page, "html.parser")
        title = soup.find("h1")
        title = title.text.strip() if title else "Unknown"

        body = soup.get_text(" ", strip=True)
        salary = extract_salary(body)

        if not is_ruby(body):
            continue
        if not is_valid_role(title):
            continue

        jobs.append({
            "title": title,
            "link": link,
            "company": "himalayas",
            "salary": salary or "N/A"
        })

    log("HIMALAYAS", f"valid jobs: {len(jobs)}")
    return jobs

# ---------------- MAIN ----------------
async def main():
    log("SYSTEM", "Starting Job Engine")

    store = load_companies()

    async with aiohttp.ClientSession() as session:

        jobs = []

        # Himalayas
        jobs += await fetch_himalayas(session)

        # Greenhouse / Lever / Ashby / Workable (sample companies list)
        greenhouse_companies = ["gitlab", "coinbase", "shopify"]
        lever_companies = ["lever", "zoom", "figma"]
        ashby_companies = ["ashby", "stripe"]
        workable_companies = ["automattic"]

        for c in greenhouse_companies:
            jobs += await fetch_greenhouse(session, c)

        for c in lever_companies:
            jobs += await fetch_lever(session, c)

        for c in ashby_companies:
            jobs += await fetch_ashby(session, c)

        for c in workable_companies:
            jobs += await fetch_workable(session, c)

    # dedupe
    seen = set()
    final = []
    for j in jobs:
        if j["link"] in seen:
            continue
        seen.add(j["link"])
        final.append(j)

    log("SYSTEM", f"total jobs: {len(final)}")

    # group email
    grouped = {}
    for j in final:
        grouped.setdefault(j["company"], []).append(j)

    body = f"🔥 RUBY JOBS - {datetime.now().strftime('%Y-%m-%d')}\n\n"

    for company, js in grouped.items():
        body += f"=== {company.upper()} ===\n"
        for j in js:
            body += f"{j['title']}\n{j['salary']}\n{j['link']}\n\n"

    msg = MIMEText(body)
    msg["Subject"] = "Ruby Jobs"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(EMAIL_SENDER, EMAIL_PASSWORD)
            s.send_message(msg)
        log("EMAIL", f"sent {len(final)} jobs")
    except Exception as e:
        log("EMAIL", str(e))

    save_companies(store)
    log("SYSTEM", "DONE")

# ---------------- RUN ----------------
if __name__ == "__main__":
    asyncio.run(main())
