import os
import json
import asyncio
import aiohttp
import logging
import re
from collections import defaultdict
from urllib.parse import urlparse

# =========================
# LOGGING (IMPORTANT FIX)
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# =========================
# CONFIG
# =========================

COMPANIES_FILE = "companies.json"

INCLUDE_TITLE_KEYWORDS = [
    "software engineer",
    "software developer",
    "backend engineer",
    "backend developer",
    "engineer",
    "developer"
]

STRICT_EXCLUDE_KEYWORDS = [
    "staff",
    "principal",
    "vp",
    "vice president",
    "director",
    "lead",
    "manager",
    "head",
    "architect",
    "junior",
    "intern"
]

CANADA_KEYWORDS = [
    "canada",
    "remote canada",
    "vancouver",
    "ca"
]

# =========================
# COMPANY STORAGE
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
# DEBUG HELPERS
# =========================

def debug_job(reason, job):
    logging.info(f"[FILTER-{reason}] {job.get('title')} | {job.get('url')}")

# =========================
# COMPANY DETECTION
# =========================

def detect_company_from_url(url: str):
    try:
        domain = urlparse(url).netloc.lower()
        path = urlparse(url).path.strip("/").split("/")

        if "greenhouse.io" in domain and path:
            return {"platform": "greenhouse", "slug": path[0]}

        if "lever.co" in domain and path:
            return {"platform": "lever", "slug": path[0]}

        if "ashbyhq.com" in domain and path:
            return {"platform": "ashby", "slug": path[0]}

        if "workable.com" in domain and path:
            return {"platform": "workable", "slug": path[0]}

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
            logging.info(f"[NEW COMPANY DISCOVERED] {detected}")
            companies.append(detected)
            seen.add(key)

    return companies

# =========================
# SALARY EXTRACTION (FIXED)
# =========================

def extract_salary(text: str):
    if not text:
        return "Not specified"

    t = text.replace(",", "")

    patterns = [
        r"\$\d{2,3},?\d{0,3}\s?[—-]\s?\$\d{2,3},?\d{0,3}",  # Coinbase style
        r"\$\d{2,3}k\s?[—-]\s?\$\d{2,3}k",
        r"\$\d{2,6}\s?[—-]\s?\$\d{2,6}",
        r"\$\d{2,6}"
    ]

    for p in patterns:
        m = re.search(p, t)
        if m:
            return m.group(0)

    if "competitive" in t.lower():
        return "Competitive"

    return "Not specified"

# =========================
# FILTERING (STRICT FIX)
# =========================

def is_senior_engineer(title: str):
    t = title.lower()

    if any(x in t for x in STRICT_EXCLUDE_KEYWORDS):
        return False

    return any(x in t for x in INCLUDE_TITLE_KEYWORDS)

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
        async with session.get(url, timeout=25) as r:
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
        url = j.get("absolute_url")

        job = {
            "company": slug,
            "title": title,
            "url": url,
            "salary": extract_salary(desc),
            "source": "greenhouse"
        }

        # DEBUG ALWAYS (key fix)
        logging.info(f"[CHECK GREENHOUSE] {title} | {url}")

        if not is_senior_engineer(title):
            debug_job("REJECT_TITLE", job)
            continue

        if not is_canada_friendly(title + " " + desc):
            debug_job("REJECT_LOCATION", job)
            continue

        debug_job("ACCEPTED", job)
        jobs.append(job)

    logging.info(f"[GREENHOUSE {slug}] -> {len(jobs)}")
    return jobs

# =========================
# LEVER
# =========================

async def fetch_lever(session, slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    logging.info(f"[LEVER] {slug}")

    try:
        async with session.get(url, timeout=25) as r:
            if r.status != 200:
                return []
            data = await r.json()

    except Exception as e:
        logging.error(f"[LEVER ERROR {slug}] {e}")
        return []

    jobs = []

    for j in data:
        title = j.get("text", "")
        desc = j.get("description", "")
        url = j.get("hostedUrl")

        job = {
            "company": slug,
            "title": title,
            "url": url,
            "salary": extract_salary(desc),
            "source": "lever"
        }

        logging.info(f"[CHECK LEVER] {title} | {url}")

        if not is_senior_engineer(title):
            debug_job("REJECT_TITLE", job)
            continue

        debug_job("ACCEPTED", job)
        jobs.append(job)

    logging.info(f"[LEVER {slug}] -> {len(jobs)}")
    return jobs

# =========================
# HIMALAYAS (SAFE SCRAPE)
# =========================

async def fetch_himalayas(session):
    url = "https://himalayas.app/jobs?search=software%20engineer"
    logging.info("[HIMALAYAS] scraping")

    try:
        async with session.get(url, timeout=25) as r:
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

        job = {
            "company": "himalayas",
            "title": "Himalayas Job",
            "url": "https://himalayas.app" + m,
            "salary": "Not specified",
            "source": "himalayas"
        }

        logging.info(f"[CHECK HIMALAYAS] {job['url']}")
        jobs.append(job)

    logging.info(f"[HIMALAYAS] -> {len(jobs)}")
    return jobs

# =========================
# GROUP EMAIL
# =========================

def group_jobs(jobs):
    grouped = defaultdict(list)

    for j in jobs:
        grouped[j["company"]].append(j)

    return grouped

def format_email(grouped):
    out = []

    for company, jobs in grouped.items():
        out.append(f"\n🏢 {company.upper()}")

        for j in jobs:
            out.append(
                f"- {j['title']}\n"
                f"  💰 {j['salary']}\n"
                f"  🔗 {j['url']}\n"
            )

    return "\n".join(out)

# =========================
# EMAIL
# =========================

def send_email(body, count):
    import smtplib
    from email.mime.text import MIMEText

    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")
    receiver = os.getenv("EMAIL_RECEIVER")

    msg = MIMEText(body)
    msg["Subject"] = f"Senior Engineer Jobs - {count}"
    msg["From"] = sender
    msg["To"] = receiver

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(sender, password)
            s.sendmail(sender, receiver, msg.as_string())

        logging.info(f"[EMAIL] Sent {count} jobs")

    except Exception as e:
        logging.error(f"[EMAIL ERROR] {e}")

# =========================
# MAIN
# =========================

async def main():
    logging.info("[SYSTEM] Starting Job Engine")

    companies = load_companies()

    async with aiohttp.ClientSession() as session:

        tasks = []

        for c in companies:
            if c["platform"] == "greenhouse":
                tasks.append(fetch_greenhouse(session, c["slug"]))
            if c["platform"] == "lever":
                tasks.append(fetch_lever(session, c["slug"]))

        tasks.append(fetch_himalayas(session))

        results = await asyncio.gather(*tasks)

    all_jobs = [j for sub in results for j in sub]

    logging.info(f"[DONE] Total jobs: {len(all_jobs)}")

    companies = update_companies(companies, all_jobs)
    save_companies(companies)

    grouped = group_jobs(all_jobs)
    email = format_email(grouped)

    send_email(email, len(all_jobs))

if __name__ == "__main__":
    asyncio.run(main())
