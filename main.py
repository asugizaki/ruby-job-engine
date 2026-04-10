import os
import json
import asyncio
import aiohttp
import logging
import re
from collections import defaultdict
from urllib.parse import urlparse

# =========================
# CONFIG
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

COMPANIES_FILE = "companies.json"

SENIOR_INCLUDE = ["senior", "sr", "mid-senior", "software engineer", "backend engineer"]
SENIOR_EXCLUDE = ["staff", "principal", "vp", "director", "lead", "junior", "intern"]

CANADA_KEYWORDS = ["canada", "remote canada", "ca", "vancouver", "remote"]

# =========================
# LOAD / SAVE COMPANIES
# =========================

def load_companies():
    if not os.path.exists(COMPANIES_FILE):
        return []
    with open(COMPANIES_FILE, "r") as f:
        return json.load(f)

def save_companies(companies):
    with open(COMPANIES_FILE, "w") as f:
        json.dump(companies, f, indent=2)

# =========================
# COMPANY DETECTION
# =========================

def detect_company_from_url(url: str):
    try:
        domain = urlparse(url).netloc.lower()
        path = urlparse(url).path.strip("/").split("/")

        if "greenhouse.io" in domain:
            return {"platform": "greenhouse", "slug": path[0]} if path else None

        if "lever.co" in domain:
            return {"platform": "lever", "slug": path[0]} if path else None

        if "ashbyhq.com" in domain:
            return {"platform": "ashby", "slug": path[0]} if path else None

        if "workable.com" in domain:
            return {"platform": "workable", "slug": path[0]} if path else None

    except:
        return None

    return None

def update_companies(companies, jobs):
    seen = set((c["platform"], c["slug"]) for c in companies)

    for job in jobs:
        detected = detect_company_from_url(job["url"])
        if not detected:
            continue

        key = (detected["platform"], detected["slug"])

        if key not in seen:
            logging.info(f"[NEW COMPANY] {detected}")
            companies.append(detected)
            seen.add(key)

    return companies

# =========================
# SALARY EXTRACTION
# =========================

def extract_salary(text: str):
    if not text:
        return "Not specified"

    t = text.replace(",", "")

    patterns = [
        r"\$\d{2,3}k\s?[-–]\s?\$\d{2,3}k",
        r"\$\d{2,3}k",
        r"\$\d{5,6}\s?[-–]\s?\$\d{5,6}",
        r"\d{2,3}k\s?[-–]\s?\d{2,3}k"
    ]

    for p in patterns:
        m = re.search(p, t, re.IGNORECASE)
        if m:
            return m.group(0)

    if "competitive" in t.lower():
        return "Competitive"

    return "Not specified"

# =========================
# FILTERING
# =========================

def is_senior(title: str):
    t = title.lower()
    if any(x in t for x in SENIOR_EXCLUDE):
        return False
    return any(x in t for x in SENIOR_INCLUDE)

def is_canada_friendly(text: str):
    if not text:
        return True
    t = text.lower()
    return any(k in t for k in CANADA_KEYWORDS) or "remote" in t

# =========================
# GREENHOUSE
# =========================

async def fetch_greenhouse(session, slug):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    logging.info(f"[GREENHOUSE] {slug}")

    try:
        async with session.get(url, timeout=20) as r:
            if r.status != 200:
                logging.warning(f"[GREENHOUSE {slug}] HTTP {r.status}")
                return []

            data = await r.json()

    except Exception as e:
        logging.error(f"[GREENHOUSE ERROR {slug}] {e}")
        return []

    jobs = []

    for j in data.get("jobs", []):
        title = j.get("title", "")
        desc = j.get("content", "")

        if not is_senior(title):
            continue

        if not is_canada_friendly(title + " " + desc):
            continue

        jobs.append({
            "company": slug,
            "title": title,
            "url": j.get("absolute_url"),
            "salary": extract_salary(desc),
            "source": "greenhouse"
        })

    logging.info(f"[GREENHOUSE {slug}] -> {len(jobs)}")
    return jobs

# =========================
# LEVER
# =========================

async def fetch_lever(session, slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    logging.info(f"[LEVER] {slug}")

    try:
        async with session.get(url, timeout=20) as r:
            if r.status != 200:
                return []

            data = await r.json()

    except Exception as e:
        logging.error(f"[LEVER ERROR {slug}] {e}")
        return []

    jobs = []

    for j in data:
        title = j.get("text", "")

        if not is_senior(title):
            continue

        jobs.append({
            "company": slug,
            "title": title,
            "url": j.get("hostedUrl"),
            "salary": extract_salary(j.get("description", "")),
            "source": "lever"
        })

    logging.info(f"[LEVER {slug}] -> {len(jobs)}")
    return jobs

# =========================
# HIMALAYAS (FIXED SCRAPER)
# =========================

async def fetch_himalayas(session):
    url = "https://himalayas.app/jobs?search=ruby"
    logging.info("[HIMALAYAS] scraping")

    try:
        async with session.get(url, timeout=20) as r:
            html = await r.text()
    except Exception as e:
        logging.error(f"[HIMALAYAS ERROR] {e}")
        return []

    matches = re.findall(r'href="(/jobs/[^"]+)"', html)

    jobs = []
    seen = set()

    for m in matches:
        if m in seen:
            continue
        seen.add(m)

        jobs.append({
            "company": "himalayas",
            "title": "Himalayas Job",
            "url": "https://himalayas.app" + m,
            "salary": "Not specified",
            "source": "himalayas"
        })

    logging.info(f"[HIMALAYAS] -> {len(jobs)}")
    return jobs

# =========================
# EMAIL GROUPING
# =========================

def group_jobs(jobs):
    grouped = defaultdict(list)

    for job in jobs:
        grouped[job["company"]].append(job)

    return grouped

def format_email(grouped):
    email = []

    for company, jobs in grouped.items():
        email.append(f"\n🏢 {company.upper()}")

        for j in jobs:
            email.append(
                f"- {j['title']}\n"
                f"  💰 {j['salary']}\n"
                f"  🔗 {j['url']}\n"
            )

    return "\n".join(email)

# =========================
# MAIN ENGINE
# =========================

async def main():
    logging.info("[SYSTEM] Starting Job Engine")

    companies = load_companies()

    async with aiohttp.ClientSession() as session:

        tasks = []

        # Greenhouse
        for c in companies:
            if c["platform"] == "greenhouse":
                tasks.append(fetch_greenhouse(session, c["slug"]))

        # Lever
        for c in companies:
            if c["platform"] == "lever":
                tasks.append(fetch_lever(session, c["slug"]))

        # Himalayas
        tasks.append(fetch_himalayas(session))

        results = await asyncio.gather(*tasks)

    all_jobs = [job for sub in results for job in sub]

    logging.info(f"[DONE] Total jobs: {len(all_jobs)}")

    # update companies.json dynamically
    companies = update_companies(companies, all_jobs)
    save_companies(companies)

    grouped = group_jobs(all_jobs)
    email_body = format_email(grouped)

    # EMAIL (same env vars you already use)
    send_email(email_body, len(all_jobs))

# =========================
# EMAIL SENDER (YOUR OLD STYLE)
# =========================

def send_email(body, count):
    import smtplib
    from email.mime.text import MIMEText

    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")
    receiver = os.getenv("EMAIL_RECEIVER")

    msg = MIMEText(body)
    msg["Subject"] = f"Job Engine - {count} Senior Jobs"
    msg["From"] = sender
    msg["To"] = receiver

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())

        logging.info(f"[EMAIL] Sent {count} jobs")

    except Exception as e:
        logging.error(f"[EMAIL ERROR] {e}")

# =========================
# RUN
# =========================

if __name__ == "__main__":
    asyncio.run(main())
