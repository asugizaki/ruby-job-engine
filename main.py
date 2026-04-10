import asyncio
import aiohttp
import json
import logging
import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime

# =========================
# CONFIG
# =========================

COMPANIES_FILE = "companies.json"
OUTPUT_FILE = "output_jobs.json"

KEYWORDS = ["ruby", "rails", "ruby on rails", "ror", "backend"]

REMOTE_BOARD_SOURCES = [
    "https://remotive.com/api/remote-jobs",
    "https://remoteok.com/remote-ruby-jobs.json"
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# =========================
# EMAIL CONFIG
# =========================

EMAIL_ENABLED = all([
    os.getenv("EMAIL_HOST"),
    os.getenv("EMAIL_USER"),
    os.getenv("EMAIL_PASS"),
    os.getenv("EMAIL_TO")
])

EMAIL_HOST = os.getenv("EMAIL_HOST")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")


def send_email(jobs):
    if not EMAIL_ENABLED:
        logging.warning("[EMAIL] Missing env vars, skipping email")
        return

    if not jobs:
        logging.info("[EMAIL] No jobs to send")
        return

    body = "\n\n".join([
        f"{j['title']} ({j['company']})\n{j['url']}"
        for j in jobs
    ])

    msg = MIMEText(body)
    msg["Subject"] = f"New Ruby Jobs Found ({len(jobs)})"
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO

    try:
        with smtplib.SMTP_SSL(EMAIL_HOST, 465) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)

        logging.info(f"[EMAIL] Sent {len(jobs)} jobs")

    except Exception as e:
        logging.error(f"[EMAIL] Failed: {e}")


# =========================
# UTILITIES
# =========================

def load_companies():
    with open(COMPANIES_FILE, "r") as f:
        return json.load(f)

def save_output(jobs):
    with open(OUTPUT_FILE, "w") as f:
        json.dump(jobs, f, indent=2)

def is_relevant(text):
    if not text:
        return False
    text = text.lower()
    return any(k in text for k in KEYWORDS)

def make_id(company, title):
    return f"{company}-{title}".lower()


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
# ATS SOURCES
# =========================

async def greenhouse(session, slug):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    logging.info(f"[GREENHOUSE] {slug}")

    data = await fetch_json(session, url)
    if not data:
        return []

    jobs = []
    for j in data.get("jobs", []):
        if is_relevant(j.get("title", "") + j.get("content", "")):
            jobs.append({
                "company": slug,
                "title": j.get("title"),
                "url": j.get("absolute_url"),
                "source": "greenhouse"
            })

    logging.info(f"[GREENHOUSE] {slug} -> {len(jobs)}")
    return jobs


async def lever(session, slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    logging.info(f"[LEVER] {slug}")

    data = await fetch_json(session, url)
    if not data:
        return []

    jobs = []
    for j in data:
        if is_relevant(j.get("text", "") + j.get("descriptionPlain", "")):
            jobs.append({
                "company": slug,
                "title": j.get("text"),
                "url": j.get("hostedUrl"),
                "source": "lever"
            })

    logging.info(f"[LEVER] {slug} -> {len(jobs)}")
    return jobs


async def workable(session, slug):
    url = f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"
    logging.info(f"[WORKABLE] {slug}")

    data = await fetch_json(session, url)
    if not data:
        return []

    jobs = []
    for j in data.get("results", []):
        if is_relevant(j.get("title", "") + j.get("description", "")):
            jobs.append({
                "company": slug,
                "title": j.get("title"),
                "url": j.get("url"),
                "source": "workable"
            })

    logging.info(f"[WORKABLE] {slug} -> {len(jobs)}")
    return jobs


# =========================
# REMOTE JOB BOARDS (RESTORED)
# =========================

async def remotive(session):
    url = "https://remotive.com/api/remote-jobs"
    logging.info("[REMOTIVE] fetching")

    data = await fetch_json(session, url)
    if not data:
        return []

    jobs = []
    for j in data.get("jobs", []):
        if is_relevant(j.get("title", "") + j.get("description", "")):
            jobs.append({
                "company": j.get("company_name"),
                "title": j.get("title"),
                "url": j.get("url"),
                "source": "remotive"
            })

    logging.info(f"[REMOTIVE] -> {len(jobs)}")
    return jobs


async def remoteok(session):
    url = "https://remoteok.com/remote-ruby-jobs.json"
    logging.info("[REMOTEOK] fetching")

    data = await fetch_json(session, url)
    if not data:
        return []

    jobs = []
    for j in data:
        if isinstance(j, dict) and is_relevant(j.get("position", "") + j.get("description", "")):
            jobs.append({
                "company": j.get("company"),
                "title": j.get("position"),
                "url": j.get("url"),
                "source": "remoteok"
            })

    logging.info(f"[REMOTEOK] -> {len(jobs)}")
    return jobs


# =========================
# ROUTER
# =========================

async def fetch_company(session, c):
    try:
        p = c["platform"]
        slug = c["slug"]

        if p == "greenhouse":
            return await greenhouse(session, slug)
        if p == "lever":
            return await lever(session, slug)
        if p == "workable":
            return await workable(session, slug)

        logging.warning(f"[UNKNOWN PLATFORM] {p}")
        return []

    except Exception as e:
        logging.error(f"[COMPANY ERROR] {c}: {e}")
        return []


# =========================
# MAIN
# =========================

async def main():
    logging.info("[SYSTEM] Starting Job Engine")

    companies = load_companies()

    all_jobs = []
    seen = set()

    async with aiohttp.ClientSession() as session:

        # ATS sources
        company_tasks = [fetch_company(session, c) for c in companies]

        # job boards (RESTORED)
        board_tasks = [
            remotive(session),
            remoteok(session)
        ]

        results = await asyncio.gather(*(company_tasks + board_tasks))

        for group in results:
            for job in group:
                job_id = make_id(job["company"], job["title"])

                if job_id in seen:
                    continue

                seen.add(job_id)
                all_jobs.append(job)

    logging.info(f"[DONE] Total jobs: {len(all_jobs)}")

    save_output(all_jobs)
    send_email(all_jobs)

    logging.info("[SYSTEM] Complete")


if __name__ == "__main__":
    asyncio.run(main())
