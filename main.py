import asyncio
import aiohttp
import json
import logging
import os
import re
import smtplib
from urllib.parse import urlparse
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
# EMAIL
# =========================

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

EMAIL_ENABLED = all([EMAIL_SENDER, EMAIL_RECEIVER, EMAIL_PASSWORD])

# =========================
# SEED JOB URLS (YOUR INPUT)
# =========================

SEED_URLS = [
    "https://job-boards.greenhouse.io/novoed/jobs/7707952",
    "https://job-boards.greenhouse.io/fleetio/jobs/5095381007"
]

# =========================
# PLATFORM DETECTION
# =========================

def detect_platform(url: str):
    if "greenhouse.io" in url:
        return "greenhouse"
    if "lever.co" in url:
        return "lever"
    if "workable.com" in url:
        return "workable"
    return None


def extract_slug(url: str, platform: str):
    path = urlparse(url).path.strip("/").split("/")

    if platform == "greenhouse":
        # /company/jobs/id
        if len(path) >= 2:
            return path[0]
    if platform == "lever":
        # usually /company or /company/jobs
        return path[0] if path else None
    if platform == "workable":
        # /company or /company/j/xxx
        return path[0] if path else None

    return None


# =========================
# COMPANIES BOOTSTRAP
# =========================

def load_companies():
    if not os.path.exists(COMPANIES_FILE):
        return []
    with open(COMPANIES_FILE, "r") as f:
        return json.load(f)


def save_companies(companies):
    with open(COMPANIES_FILE, "w") as f:
        json.dump(companies, f, indent=2)


def bootstrap_companies():
    companies = load_companies()

    for url in SEED_URLS:
        platform = detect_platform(url)
        slug = extract_slug(url, platform)

        if not platform or not slug:
            logging.warning(f"[BOOTSTRAP SKIP] {url}")
            continue

        entry = {
            "platform": platform,
            "slug": slug
        }

        if entry not in companies:
            logging.info(f"[BOOTSTRAP ADD] {entry}")
            companies.append(entry)

    save_companies(companies)
    return companies


# =========================
# FILTERING (SENIOR + CANADA SAFE)
# =========================

def is_relevant(text: str) -> bool:
    if not text:
        return False

    t = text.lower()

    if not any(k in t for k in ["ruby", "rails", "ruby on rails", "ror"]):
        return False

    if not any(s in t for s in ["senior", "sr", "software engineer ii"]):
        return False

    if any(x in t for x in [
        "junior", "entry", "graduate",
        "staff", "principal", "vp", "director"
    ]):
        return False

    # Canada-safe heuristic
    blocked_geo = [
        "india only", "us only", "europe only",
        "uk only", "apac only"
    ]

    if any(x in t for x in blocked_geo):
        return False

    return True


# =========================
# UTIL
# =========================

def extract_salary(text: str):
    m = re.search(r"(\$?\d{2,3}[kK]?\s?[-–]\s?\$?\d{2,3}[kK]?)", text or "")
    return m.group(1) if m else "Not specified"


def detect_seniority(text: str):
    t = text.lower()
    if "software engineer ii" in t:
        return "Senior (II)"
    return "Senior"


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

    return jobs


# =========================
# WORKABLE (FIXED SUPPORT)
# =========================

async def workable(session, slug):
    url = f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"
    logging.info(f"[WORKABLE] {slug}")

    data = await fetch_json(session, url)
    if not data:
        return []

    jobs = []
    for j in data.get("results", []):
        text = j.get("title", "") + j.get("description", "")

        if is_relevant(text):
            jobs.append({
                "company": slug,
                "title": j.get("title"),
                "url": j.get("url"),
                "salary": "Not specified",
                "seniority": detect_seniority(text),
                "source": "workable"
            })

    return jobs


# =========================
# HIMALAYAS (RESTORED)
# =========================

async def himalayas(session):
    url = "https://himalayas.app/api/jobs?query=ruby"
    logging.info("[HIMALAYAS] fetching")

    data = await fetch_json(session, url)
    if not data:
        return []

    jobs = []
    for j in data.get("jobs", []):
        text = j.get("title", "") + j.get("description", "")

        if is_relevant(text):
            jobs.append({
                "company": j.get("company"),
                "title": j.get("title"),
                "url": j.get("url"),
                "salary": "Not specified",
                "seniority": "Senior",
                "source": "himalayas"
            })

    logging.info(f"[HIMALAYAS] -> {len(jobs)}")
    return jobs


# =========================
# EMAIL
# =========================

def send_email(jobs):
    if not EMAIL_ENABLED or not jobs:
        return

    grouped = {}

    for j in jobs:
        grouped.setdefault(j["company"], []).append(j)

    lines = []

    for company, items in grouped.items():
        lines.append(f"\n================ {company.upper()} ================\n")
        for j in items:
            lines.append(f"[{j['seniority']}] {j['title']} | {j['salary']} | {j['url']}")

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
    logging.info("[SYSTEM] Bootstrapping companies")

    bootstrap_companies()
    companies = load_companies()

    all_jobs = []

    async with aiohttp.ClientSession() as session:

        tasks = []

        for c in companies:
            if c["platform"] == "greenhouse":
                tasks.append(greenhouse(session, c["slug"]))
            elif c["platform"] == "lever":
                tasks.append(lever(session, c["slug"]))
            elif c["platform"] == "workable":
                tasks.append(workable(session, c["slug"]))

        tasks.append(himalayas(session))

        results = await asyncio.gather(*tasks)

        for group in results:
            all_jobs.extend(group)

    with open(OUTPUT_FILE, "w") as f:
        json.dump(all_jobs, f, indent=2)

    send_email(all_jobs)

    logging.info(f"[DONE] Total jobs: {len(all_jobs)}")


if __name__ == "__main__":
    asyncio.run(main())
