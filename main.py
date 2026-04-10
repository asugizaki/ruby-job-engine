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
    return any(k in text.lower() for k in GOOD_ROLE_KEYWORDS)

def is_excluded_level(text):
    return any(k in text.lower() for k in EXCLUDE_LEVELS)

def is_valid_role(title):
    t = title.lower()

    if not is_engineering_role(t):
        return False, "NOT_ENGINEER"

    if is_excluded_level(t):
        return False, "EXCLUDED_LEVEL"

    return True, "VALID"

def is_valid_location(text):
    t = text.lower()
    return any(x in t for x in ["canada", "north america", "worldwide", "anywhere"])

def extract_salary(text):
    patterns = [
        r"\$[\d,]{2,3}(?:,\d{3})*(?:\s*[-—–]\s*\$?[\d,]{2,3}(?:,\d{3})*)?",
        r"[\d,]{2,3}(?:,\d{3})*\s*(?:usd|cad)"
    ]

    for p in patterns:
        match = re.search(p, text, re.IGNORECASE)
        if match:
            return match.group(0)

    return None

# ---------------- FETCH ----------------

async def fetch(session, url):
    try:
        log("FETCH", url)
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

    log("ACCEPT", f"{url}")
    return {
        "title": title,
        "company": company,
        "link": url,
        "salary": salary or "N/A"
    }

# ---------------- HIMALAYAS ----------------

async def fetch_himalayas(session):
    log("HIMALAYAS", "Starting Canada Ruby search")

    base_url = "https://himalayas.app/jobs/countries/canada?q=ruby"
    jobs = []

    html = await fetch(session, base_url)
    if not html:
        log("HIMALAYAS", "Failed to load search page")
        return jobs

    soup = BeautifulSoup(html, "html.parser")

    job_links = set()

    # STRICT: only grab actual job links
    for a in soup.find_all("a", href=True):
        href = a["href"]

        # Only valid job pages
        if "/companies/" in href and "/jobs/" in href:
            full_url = urljoin("https://himalayas.app", href)
            job_links.add(full_url)

    log("HIMALAYAS", f"Found {len(job_links)} job links")

    # DEBUG: log all discovered URLs
    for url in job_links:
        log("HIMALAYAS-LINK", url)

    tasks = [fetch(session, url) for url in list(job_links)[:50]]
    pages = await asyncio.gather(*tasks)

    for i, html in enumerate(pages):
        url = list(job_links)[i]

        if not html:
            log("REJECT", f"{url} [NO_HTML]")
            continue

        job = parse_job(html, url)
        if job:
            jobs.append(job)

    log("HIMALAYAS", f"Accepted {len(jobs)} jobs")
    return jobs

# ---------------- MAIN ----------------

async def main():
    log("SYSTEM", "Starting Job Engine")

    async with aiohttp.ClientSession() as session:
        himalayas_jobs = await fetch_himalayas(session)

    jobs = himalayas_jobs

    log("DONE", f"Total jobs: {len(jobs)}")

    # ---------------- GROUP ----------------
    grouped = {}
    for j in jobs:
        grouped.setdefault(j["company"], []).append(j)

    # ---------------- EMAIL ----------------
    body = f"🔥 Ruby Jobs ({datetime.now().strftime('%Y-%m-%d')})\n\n"

    for company, items in grouped.items():
        body += f"=== {company.upper()} ===\n"
        for j in items:
            body += f"{j['title']}\nSalary: {j['salary']}\n{j['link']}\n\n"

    msg = MIMEText(body)
    msg["Subject"] = "🔥 Ruby Jobs"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(EMAIL_SENDER, EMAIL_PASSWORD)
        s.send_message(msg)

    log("EMAIL", f"Sent {len(jobs)} jobs")
    log("SYSTEM", "Complete")

if __name__ == "__main__":
    asyncio.run(main())
