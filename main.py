import asyncio
import aiohttp
import json
import logging
import os
from datetime import datetime

# =========================
# CONFIG
# =========================

COMPANIES_FILE = "companies.json"
OUTPUT_FILE = "output_jobs.json"

KEYWORDS = ["ruby", "rails", "ruby on rails", "backend", "ror"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# =========================
# UTILITIES
# =========================

def load_companies():
    with open(COMPANIES_FILE, "r") as f:
        return json.load(f)

def save_output(jobs):
    with open(OUTPUT_FILE, "w") as f:
        json.dump(jobs, f, indent=2)

def is_relevant(job_text):
    if not job_text:
        return False
    text = job_text.lower()
    return any(k in text for k in KEYWORDS)

def make_job_id(company, title):
    return f"{company}-{title}".lower()

# =========================
# FETCHERS
# =========================

async def fetch_json(session, url):
    try:
        async with session.get(url, timeout=20) as resp:
            if resp.status != 200:
                logging.warning(f"[HTTP {resp.status}] {url}")
                return None
            return await resp.json()
    except Exception as e:
        logging.error(f"[ERROR] Fetch failed {url}: {e}")
        return None


# -------- Greenhouse --------
async def fetch_greenhouse(session, company):
    url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
    logging.info(f"[GREENHOUSE] {company}")
    data = await fetch_json(session, url)

    if not data or "jobs" not in data:
        return []

    jobs = []
    for job in data["jobs"]:
        title = job.get("title", "")
        desc = job.get("content", "")

        if is_relevant(title + " " + desc):
            jobs.append({
                "company": company,
                "title": title,
                "url": job.get("absolute_url"),
                "source": "greenhouse"
            })

    logging.info(f"[GREENHOUSE] {company} -> {len(jobs)} jobs")
    return jobs


# -------- Lever --------
async def fetch_lever(session, company):
    url = f"https://api.lever.co/v0/postings/{company}?mode=json"
    logging.info(f"[LEVER] {company}")
    data = await fetch_json(session, url)

    if not data:
        return []

    jobs = []
    for job in data:
        title = job.get("text", "")
        desc = job.get("descriptionPlain", "")

        if is_relevant(title + " " + desc):
            jobs.append({
                "company": company,
                "title": title,
                "url": job.get("hostedUrl"),
                "source": "lever"
            })

    logging.info(f"[LEVER] {company} -> {len(jobs)} jobs")
    return jobs


# -------- Workable --------
async def fetch_workable(session, company):
    url = f"https://apply.workable.com/api/v3/accounts/{company}/jobs"
    logging.info(f"[WORKABLE] {company}")

    data = await fetch_json(session, url)

    if not data or "results" not in data:
        return []

    jobs = []
    for job in data["results"]:
        title = job.get("title", "")
        desc = job.get("description", "")

        if is_relevant(title + " " + desc):
            jobs.append({
                "company": company,
                "title": title,
                "url": job.get("url"),
                "source": "workable"
            })

    logging.info(f"[WORKABLE] {company} -> {len(jobs)} jobs")
    return jobs


# -------- Ashby --------
async def fetch_ashby(session, company):
    url = f"https://jobs.ashbyhq.com/api/posting"
    logging.info(f"[ASHBY] {company}")

    data = await fetch_json(session, url)

    if not data:
        return []

    jobs = []
    for job in data:
        title = job.get("title", "")
        desc = job.get("description", "")

        if is_relevant(title + " " + desc):
            jobs.append({
                "company": company,
                "title": title,
                "url": job.get("jobUrl"),
                "source": "ashby"
            })

    logging.info(f"[ASHBY] {company} -> {len(jobs)} jobs")
    return jobs


# =========================
# ROUTER
# =========================

async def fetch_company(session, company):
    platform = company["platform"]
    slug = company["slug"]

    try:
        if platform == "greenhouse":
            return await fetch_greenhouse(session, slug)

        if platform == "lever":
            return await fetch_lever(session, slug)

        if platform == "workable":
            return await fetch_workable(session, slug)

        if platform == "ashby":
            return await fetch_ashby(session, slug)

        logging.warning(f"[UNKNOWN PLATFORM] {platform}")
        return []

    except Exception as e:
        logging.error(f"[COMPANY ERROR] {company['name']}: {e}")
        return []


# =========================
# MAIN
# =========================

async def main():
    logging.info("[SYSTEM] Starting Ruby Job Engine")

    companies = load_companies()

    all_jobs = []
    seen = set()

    async with aiohttp.ClientSession() as session:
        tasks = [fetch_company(session, c) for c in companies]
        results = await asyncio.gather(*tasks)

        for job_list in results:
            for job in job_list:
                job_id = make_job_id(job["company"], job["title"])

                if job_id in seen:
                    continue

                seen.add(job_id)
                all_jobs.append(job)

    logging.info(f"[DONE] Total unique jobs: {len(all_jobs)}")

    save_output(all_jobs)
    logging.info(f"[SAVED] Output written to {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
