import asyncio
import aiohttp
import json
import logging
import os
import re
import smtplib
from email.mime.text import MIMEText

# =========================
# CONFIG
# =========================

COMPANIES_FILE = "companies.json"
OUTPUT_FILE = "output_jobs.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# =========================
# EMAIL (UNCHANGED SECRETS)
# =========================

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

EMAIL_ENABLED = all([EMAIL_SENDER, EMAIL_RECEIVER, EMAIL_PASSWORD])

# =========================
# FILTERING (FIXED)
# =========================

INCLUDE = [
    "ruby", "rails", "ruby on rails", "ror", "backend"
]

EXCLUDE = [
    "staff", "principal", "distinguished", "architect",
    "head of", "director", "vp", "vice president",
    "lead", "sr. staff", "staff engineer"
]

def is_relevant(text: str) -> bool:
    if not text:
        return False

    t = text.lower()

    if not any(k in t for k in INCLUDE):
        return False

    if any(e in t for e in EXCLUDE):
        return False

    return True


# =========================
# SALARY EXTRACTION (NEW)
# =========================

salary_pattern = re.compile(
    r"(\$?\d{2,3}[kK]?\s?[-–]\s?\$?\d{2,3}[kK]?)"
)

def extract_salary(text: str):
    if not text:
        return None
    match = salary_pattern.search(text)
    return match.group(1) if match else "Not specified"


# =========================
# COMPANIES (DYNAMIC FIX)
# =========================

def load_companies():
    if not os.path.exists(COMPANIES_FILE):
        return []
    with open(COMPANIES_FILE, "r") as f:
        return json.load(f)

def save_companies(companies):
    with open(COMPANIES_FILE, "w") as f:
        json.dump(companies, f, indent=2)

def add_company(companies, new):
    if not any(c["slug"] == new["slug"] and c["platform"] == new["platform"] for c in companies):
        logging.info(f"[NEW COMPANY DISCOVERED] {new}")
        companies.append(new)


# =========================
# EMAIL FORMAT (NEW GROUPING)
# =========================

def send_email(jobs):
    if not EMAIL_ENABLED:
        logging.warning("[EMAIL] Missing credentials")
        return

    if not jobs:
        return

    grouped = {}

    for j in jobs:
        grouped.setdefault(j["company"], []).append(j)

    lines = []

    for company, items in grouped.items():
        lines.append(f"\n=== {company.upper()} ===\n")

        for j in items:
            lines.append(
                f"- {j['title']} | {j['salary']} | {j['url']}"
            )

    body = "\n".join(lines)

    msg = MIMEText(body)
    msg["Subject"] = f"Ruby Jobs ({len(jobs)})"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(EMAIL_SENDER, EMAIL_PASSWORD)
        s.send_message(msg)

    logging.info(f"[EMAIL] Sent grouped jobs: {len(jobs)}")


# =========================
# HTTP
# =========================

async def fetch_json(session, url):
    try:
        async with session.get(url, timeout=25) as r:
            if r.status != 200:
                logging.warning(f"[HTTP {r.status}] {url}")
                return None
            return await r.json()
    except Exception as e:
        logging.error(f"[FETCH ERROR] {url}: {e}")
        return None


# =========================
# GREENHOUSE (FIXED SHOPIFY)
# =========================

async def greenhouse(session, slug):
    urls_to_try = [
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        f"https://boards-api.greenhouse.io/v1/boards/{slug}-inc/jobs",
        f"https://boards-api.greenhouse.io/v1/boards/{slug}inc/jobs",
    ]

    data = None

    for url in urls_to_try:
        logging.info(f"[GREENHOUSE TRY] {url}")
        data = await fetch_json(session, url)
        if data:
            break

    if not data:
        logging.warning(f"[GREENHOUSE FAILED] {slug}")
        return []

    jobs = []
    for j in data.get("jobs", []):
        text = j.get("title", "") + j.get("content", "")

        if is_relevant(text):
            jobs.append({
                "company": slug,
                "title": j.get("title"),
                "url": j.get("absolute_url"),
                "salary": extract_salary(text),
                "source": "greenhouse"
            })

    return jobs


# =========================
# LEVER / WORKABLE (UNCHANGED CORE)
# =========================

async def lever(session, slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    data = await fetch_json(session, url) or []

    jobs = []
    for j in data:
        text = j.get("text", "") + j.get("descriptionPlain", "")

        if is_relevant(text):
            jobs.append({
                "company": slug,
                "title": j.get("text"),
                "url": j.get("hostedUrl"),
                "salary": extract_salary(text),
                "source": "lever"
            })

    logging.info(f"[LEVER] {slug} -> {len(jobs)}")
    return jobs


# =========================
# REMOTE SOURCES
# =========================

async def remotive(session):
    url = "https://remotive.com/api/remote-jobs"
    data = await fetch_json(session, url) or {}

    jobs = []
    for j in data.get("jobs", []):
        text = j.get("title", "") + j.get("description", "")

        if is_relevant(text):
            jobs.append({
                "company": j.get("company_name"),
                "title": j.get("title"),
                "url": j.get("url"),
                "salary": "Not specified",
                "source": "remotive"
            })

    return jobs


# =========================
# MAIN ENGINE (AUTO DISCOVERY)
# =========================

async def main():
    logging.info("[SYSTEM] Starting Job Engine")

    companies = load_companies()
    discovered = []

    all_jobs = []

    async with aiohttp.ClientSession() as session:

        tasks = []

        for c in companies:
            if c["platform"] == "greenhouse":
                tasks.append(greenhouse(session, c["slug"]))
            if c["platform"] == "lever":
                tasks.append(lever(session, c["slug"]))

        tasks.append(remotive(session))

        results = await asyncio.gather(*tasks)

        for group in results:
            for job in group:
                all_jobs.append(job)

                # AUTO DISCOVERY
                discovered.append({
                    "platform": job.get("source", "unknown"),
                    "slug": job["company"]
                })

    # save jobs
    with open(OUTPUT_FILE, "w") as f:
        json.dump(all_jobs, f, indent=2)

    # update companies dynamically
    for c in discovered:
        add_company(companies, c)

    save_companies(companies)

    send_email(all_jobs)

    logging.info(f"[DONE] Jobs: {len(all_jobs)} | Companies updated: {len(companies)}")


if __name__ == "__main__":
    asyncio.run(main())
