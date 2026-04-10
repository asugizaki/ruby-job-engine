import asyncio
import aiohttp
import json
import re
import smtplib
import logging
import os
from urllib.parse import urljoin, urlparse
from email.mime.text import MIMEText
from datetime import datetime
from bs4 import BeautifulSoup

# ---------------- LOGGING ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def log(step, msg):
    logger.info(f"[{step}] {msg}")

# ---------------- CONFIG ----------------
RUBY_KEYWORDS = ["ruby", "rails"]

GOOD_ROLE_KEYWORDS = [
    "engineer", "developer", "backend", "back-end",
    "fullstack", "full-stack", "software"
]

EXCLUDE_LEVELS = [
    "junior", "jr", "staff", "principal", "lead", "manager", "vp", "director", "head"
]

ALLOWED_LEVELS = [
    "senior", "sr", "intermediate", "mid", "mid-level"
]

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

# ---------------- HELPERS ----------------

def contains_ruby(text):
    return any(k in text.lower() for k in RUBY_KEYWORDS)

def is_engineering_role(text):
    t = text.lower()
    return any(k in t for k in GOOD_ROLE_KEYWORDS)

def is_excluded_level(text):
    t = text.lower()
    return any(k in t for k in EXCLUDE_LEVELS)

def is_allowed_level(text):
    t = text.lower()
    return any(k in t for k in ALLOWED_LEVELS)

def is_valid_role(title):
    t = title.lower()

    if not is_engineering_role(t):
        return False, "NOT_ENGINEER"

    if is_excluded_level(t):
        return False, "EXCLUDED_LEVEL"

    # allow senior OR intermediate OR unspecified
    return True, "VALID"

def is_valid_location(text):
    t = text.lower()

    if any(x in t for x in ["canada", "north america", "worldwide", "anywhere"]):
        return True

    return False

def extract_salary(text):
    patterns = [
        r"\$[\d,]{2,3}(?:,\d{3})*(?:\s*[-—–]\s*\$?[\d,]{2,3}(?:,\d{3})*)?",
        r"[\d,]{2,3}(?:,\d{3})*\s*(?:usd|cad)",
    ]

    for p in patterns:
        match = re.search(p, text, re.IGNORECASE)
        if match:
            return match.group(0)

    return None

# ---------------- FETCH ----------------

async def fetch(session, url):
    try:
        async with session.get(url, timeout=20) as resp:
            if resp.status != 200:
                log("HTTP", f"{resp.status} {url}")
                return None
            return await resp.text()
    except Exception as e:
        log("ERROR", f"{url} -> {e}")
        return None

# ---------------- PARSE ----------------

def parse_job(html, url):
    soup = BeautifulSoup(html, "html.parser")

    title = None
    if soup.find("h1"):
        title = soup.find("h1").get_text(strip=True)

    if not title:
        title = soup.title.string if soup.title else ""

    text = soup.get_text(" ", strip=True)

    company = urlparse(url).netloc
    salary = extract_salary(text)

    log("PARSE", f"{url}")
    log("PARSE", f"Title: {title}")
    log("PARSE", f"Salary: {salary}")

    # ---------------- FILTERS ----------------

    valid_role, reason = is_valid_role(title)
    if not valid_role:
        log("REJECT", f"{url} [{reason}]")
        return None

    if not contains_ruby(text):
        log("REJECT", f"{url} [NOT_RUBY]")
        return None

    if not is_valid_location(text):
        log("REJECT", f"{url} [NOT_CANADA]")
        return None

    log("ACCEPT", f"{title}")

    return {
        "title": title,
        "company": company,
        "link": url,
        "salary": salary or "N/A"
    }

# ---------------- HIMALAYAS ----------------

async def fetch_himalayas(session):
    log("HIMALAYAS", "fetching")
    jobs = []

    base = "https://himalayas.app/jobs/countries/canada?q=ruby"

    html = await fetch(session, base)
    if not html:
        return jobs

    soup = BeautifulSoup(html, "html.parser")

    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/jobs/" in href:
            full = urljoin("https://himalayas.app", href)
            links.append(full)

    links = list(set(links))
    log("HIMALAYAS", f"found {len(links)} links")

    tasks = [fetch(session, u) for u in links[:30]]
    pages = await asyncio.gather(*tasks)

    for i, html in enumerate(pages):
        if html:
            job = parse_job(html, links[i])
            if job:
                jobs.append(job)

    log("HIMALAYAS", f"accepted {len(jobs)}")
    return jobs

# ---------------- GREENHOUSE ----------------

GREENHOUSE_COMPANIES = [
    "gitlab",
    "coinbase",
    "novoed",
    "fleetio"
]

async def fetch_greenhouse(session):
    jobs = []

    for company in GREENHOUSE_COMPANIES:
        url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
        log("GREENHOUSE", company)

        data = await fetch(session, url)
        if not data:
            continue

        data = json.loads(data)

        for job in data.get("jobs", []):
            title = job.get("title", "")
            location = job.get("location", {}).get("name", "")

            combined = f"{title} {location}"

            valid_role, reason = is_valid_role(title)
            if not valid_role:
                log("REJECT", f"{title} [{reason}]")
                continue

            if not contains_ruby(title):
                continue

            if not is_valid_location(location):
                continue

            jobs.append({
                "title": title,
                "company": company,
                "link": job.get("absolute_url"),
                "salary": "N/A"
            })

    log("GREENHOUSE", f"{len(jobs)} jobs")
    return jobs

# ---------------- MAIN ----------------

async def main():
    log("SYSTEM", "Starting Job Engine")

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            fetch_greenhouse(session),
            fetch_himalayas(session)
        )

    jobs = [j for group in results for j in group]

    log("DONE", f"Total jobs: {len(jobs)}")

    # ---------------- GROUP BY COMPANY ----------------
    grouped = {}
    for j in jobs:
        grouped.setdefault(j["company"], []).append(j)

    # ---------------- EMAIL ----------------
    body = f"🔥 Ruby Jobs ({datetime.now().strftime('%Y-%m-%d')})\n\n"

    for company, items in grouped.items():
        body += f"=== {company.upper()} ===\n"
        for j in items:
            body += f"{j['title']}\n{j['salary']}\n{j['link']}\n\n"

    msg = MIMEText(body)
    msg["Subject"] = "🔥 Ruby Jobs"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(EMAIL_SENDER, EMAIL_PASSWORD)
        s.send_message(msg)

    log("EMAIL", f"Sent {len(jobs)} jobs")

if __name__ == "__main__":
    asyncio.run(main())
