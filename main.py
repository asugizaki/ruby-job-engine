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
# EMAIL (GitHub Secrets)
# =========================

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

EMAIL_ENABLED = all([EMAIL_SENDER, EMAIL_RECEIVER, EMAIL_PASSWORD])

# =========================
# FILTER (SENIOR ONLY)
# =========================

def is_relevant(text: str) -> bool:
    if not text:
        return False

    t = text.lower()

    # MUST be Ruby related
    if not any(k in t for k in ["ruby", "rails", "ruby on rails", "ror"]):
        return False

    # MUST be senior level
    senior_signals = [
        "senior", "sr", "software engineer ii", "engineer ii"
    ]

    if not any(s in t for s in senior_signals):
        return False

    # EXCLUDE non-senior / leadership noise
    excludes = [
        "junior", "jr", "entry", "graduate",
        "staff", "principal", "distinguished",
        "vp", "vice president", "director", "head of",
        "intern"
    ]

    if any(x in t for x in excludes):
        return False

    return True


# =========================
# SALARY EXTRACTION
# =========================

salary_pattern = re.compile(r"(\$?\d{2,3}[kK]?\s?[-–]\s?\$?\d{2,3}[kK]?)")

def extract_salary(text: str):
    if not text:
        return "Not specified"
    m = salary_pattern.search(text)
    return m.group(1) if m else "Not specified"


# =========================
# SENIORITY TAG
# =========================

def detect_seniority(text: str):
    t = text.lower()

    if "software engineer ii" in t or "engineer ii" in t:
        return "Senior (II)"
    return "Senior"


# =========================
# COMPANIES (DYNAMIC)
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
        logging.info(f"[NEW COMPANY] {new}")
        companies.append(new)


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
# GREENHOUSE
# =========================

async def greenhouse(session, slug):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    logging.info(f"[GREENHOUSE] {slug}")

    data = await fetch_json(session, url)
    if not data:
        return []

    jobs = []
    for j in data.get("jobs", []):
        text = (j.get("title", "") or "") + (j.get("content", "") or "")

        if is_relevant(text):
            jobs.append({
                "company": slug,
                "title": j.get("title"),
                "url": j.get("absolute_url"),
                "salary": extract_salary(text),
                "seniority": detect_seniority(text),
                "source": "greenhouse"
            })

    logging.info(f"[GREENHOUSE] {slug} -> {len(jobs)}")
    return jobs


# =========================
# LEVER
# =========================

async def lever(session, slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    logging.info(f"[LEVER] {slug}")

    data = await fetch_json(session, url) or []

    jobs = []
    for j in data:
        text = (j.get("text", "") or "") + (j.get("descriptionPlain", "") or "")

        if is_relevant(text):
            jobs.append({
                "company": slug,
                "title": j.get("text"),
                "url": j.get("hostedUrl"),
                "salary": extract_salary(text),
                "seniority": detect_seniority(text),
                "source": "lever"
            })

    logging.info(f"[LEVER] {slug} -> {len(jobs)}")
    return jobs


# =========================
# REMOTE SOURCES
# =========================

async def remotive(session):
    url = "https://remotive.com/api/remote-jobs"
    logging.info("[REMOTIVE] fetching")

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
                "seniority": "Senior",
                "source": "remotive"
            })

    logging.info(f"[REMOTIVE] -> {len(jobs)}")
    return jobs


async def remoteok(session):
    url = "https://remoteok.com/remote-ruby-jobs.json"
    logging.info("[REMOTEOK] fetching")

    data = await fetch_json(session, url) or []

    jobs = []
    for j in data:
        if isinstance(j, dict):
            text = j.get("position", "") + j.get("description", "")

            if is_relevant(text):
                jobs.append({
                    "company": j.get("company"),
                    "title": j.get("position"),
                    "url": j.get("url"),
                    "salary": "Not specified",
                    "seniority": detect_seniority(text),
                    "source": "remoteok"
                })

    logging.info(f"[REMOTEOK] -> {len(jobs)}")
    return jobs


# =========================
# EMAIL
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
        lines.append(f"\n================ {company.upper()} ================\n")

        for j in items:
            lines.append(
                f"[{j['seniority']}] {j['title']} | {j['salary']} | {j['url']}"
            )

    body = "\n".join(lines)

    msg = MIMEText(body)
    msg["Subject"] = f"Senior Ruby Jobs ({len(jobs)})"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(EMAIL_SENDER, EMAIL_PASSWORD)
        s.send_message(msg)

    logging.info(f"[EMAIL] Sent {len(jobs)} jobs")


# =========================
# MAIN
# =========================

async def main():
    logging.info("[SYSTEM] Starting Job Engine")

    companies = load_companies()
    all_jobs = []
    discovered = []

    async with aiohttp.ClientSession() as session:

        tasks = []

        for c in companies:
            if c["platform"] == "greenhouse":
                tasks.append(greenhouse(session, c["slug"]))
            elif c["platform"] == "lever":
                tasks.append(lever(session, c["slug"]))

        tasks.append(remotive(session))
        tasks.append(remoteok(session))

        results = await asyncio.gather(*tasks)

        for group in results:
            for job in group:
                all_jobs.append(job)
                discovered.append({
                    "platform": job.get("source", "unknown"),
                    "slug": job["company"]
                })

    # save jobs
    with open(OUTPUT_FILE, "w") as f:
        json.dump(all_jobs, f, indent=2)

    # auto-update companies
    for c in discovered:
        add_company(companies, c)

    save_companies(companies)

    send_email(all_jobs)

    logging.info(f"[DONE] {len(all_jobs)} senior Ruby jobs")


if __name__ == "__main__":
    asyncio.run(main())
