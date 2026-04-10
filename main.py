# ELITE RUBY JOB INTELLIGENCE SYSTEM (ASYNC + LOGGING + COMPANY MEMORY)
# --------------------------------------------------------------------

import asyncio
import aiohttp
import json
import smtplib
import logging
import os
from urllib.parse import urljoin, urlparse
from email.mime.text import MIMEText
from datetime import datetime
from bs4 import BeautifulSoup
import feedparser

# ---------------- LOGGING ----------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

def log(step, msg, data=None):
    if data is not None:
        logger.info(f"[{step}] {msg} | {data}")
    else:
        logger.info(f"[{step}] {msg}")

# ---------------- CONFIG ----------------
RUBY_KEYWORDS = ["ruby", "rails"]
EXCLUDE_KEYWORDS = ["staff", "principal", "director", "head"]

COMPANY_STORE_FILE = "companies.json"

RSS_FEEDS = [
    "https://remoteok.com/remote-ruby-jobs.rss",
    "https://rubyonremote.com/remote-ruby-jobs.rss",
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://remotive.com/remote-jobs/software-dev.rss",
    "https://startup.jobs/rss",
]

SEARCH_PAGES = [
    "https://remoteok.com/remote-ruby-jobs",
    "https://weworkremotely.com/remote-jobs/search?term=ruby",
    "https://himalayas.app/jobs?q=ruby",
]

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVER:
    raise ValueError("Missing email environment variables")

CACHE = set()

# ---------------- COMPANY STORAGE ----------------

def load_companies():
    try:
        with open(COMPANY_STORE_FILE, "r") as f:
            data = json.load(f)
            log("STATE", "Loaded companies", {k: len(v) for k, v in data.items()})
            return data
    except Exception as e:
        log("STATE", f"No existing file, creating new | {e}")
        return {"greenhouse": [], "lever": [], "ashby": []}


def save_companies(data):
    log("STATE", "Saving companies", {k: len(v) for k, v in data.items()})

    with open(COMPANY_STORE_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ---------------- PLATFORM DETECTION ----------------

def detect_platform(url):
    u = url.lower()

    if "greenhouse" in u:
        return "greenhouse"
    if "lever" in u:
        return "lever"
    if "ashby" in u:
        return "ashby"

    return None

# ---------------- COMPANY EXTRACTION ----------------

def extract_company_from_url(url):
    try:
        url = url.split("?")[0].rstrip("/")
        parts = url.split("/")

        log("PARSE", "Analyzing URL", url)

        # GREENHOUSE
        if "greenhouse" in url and "boards.greenhouse.io" in url:
            idx = parts.index("boards.greenhouse.io")
            company = parts[idx + 1]
            log("PARSE", "Greenhouse company extracted", company)
            return company

        # LEVER
        if "lever.co" in url:
            idx = parts.index("jobs.lever.co")
            company = parts[idx + 1]
            log("PARSE", "Lever company extracted", company)
            return company

        # ASHBY
        if "ashby" in url:
            idx = [i for i, p in enumerate(parts) if "ashby" in p][0]
            company = parts[idx + 1]
            log("PARSE", "Ashby company extracted", company)
            return company

        log("PARSE", "No match for platform URL", url)
        return None

    except Exception as e:
        log("PARSE", "FAILED extracting company", {"url": url, "error": str(e)})
        return None

# ---------------- DISCOVERY ----------------

def discover_company(raw_url, store):
    if not raw_url:
        return

    url = raw_url.strip()

    # skip garbage
    if url.startswith("/"):
        log("DISCOVERY", "Skipping relative URL", url)
        return

    if "http" not in url:
        log("DISCOVERY", "Skipping invalid URL", url)
        return

    log("DISCOVERY", "Checking URL", url)

    platform = detect_platform(url)

    if not platform:
        log("DISCOVERY", "No platform detected", url)
        return

    company = extract_company_from_url(url)

    if not company:
        log("DISCOVERY", "No company extracted", url)
        return

    if company in store[platform]:
        log("DISCOVERY", "Already exists", {"company": company, "platform": platform})
        return

    store[platform].append(company)

    log("DISCOVERY", "NEW COMPANY ADDED", {
        "company": company,
        "platform": platform,
        "url": url
    })

# ---------------- HELPERS ----------------

def contains_ruby(text):
    return any(k in text.lower() for k in RUBY_KEYWORDS)

def is_excluded(text):
    return any(k in text.lower() for k in EXCLUDE_KEYWORDS)

def is_remote(text):
    return "remote" in text.lower()

# ---------------- HTTP ----------------

async def fetch(session, url):
    try:
        log("FETCH", url)
        async with session.get(url, timeout=20) as resp:
            return await resp.text()
    except Exception as e:
        log("HTTP", f"Failed {url}", str(e))
        return None

# ---------------- RSS ----------------

def fetch_rss_jobs():
    jobs = []
    log("RSS", "Scanning feeds")

    for feed in RSS_FEEDS:
        try:
            data = feedparser.parse(feed)

            for entry in data.entries:
                text = entry.get("title", "") + entry.get("summary", "")

                if contains_ruby(text) and is_remote(text) and not is_excluded(text):
                    jobs.append({
                        "title": entry.get("title"),
                        "link": entry.get("link"),
                        "company": "rss"
                    })

            log("RSS", f"{feed} -> {len(data.entries)} entries")

        except Exception as e:
            log("RSS", "Error feed", str(e))

    log("RSS", f"Total jobs: {len(jobs)}")
    return jobs

# ---------------- SEARCH ----------------

async def fetch_search_jobs():
    jobs = []

    async with aiohttp.ClientSession() as session:

        tasks = [fetch(session, url) for url in SEARCH_PAGES]
        pages = await asyncio.gather(*tasks)

        job_urls = []

        for idx, html in enumerate(pages):
            if not html:
                continue

            base_url = SEARCH_PAGES[idx]
            soup = BeautifulSoup(html, "html.parser")

            for a in soup.find_all("a", href=True):
                href = urljoin(base_url, a["href"])
                title = a.get_text(strip=True)

                log("DISCOVERY", "Found link", {"raw": a["href"], "full": href})

                if len(title) < 5:
                    continue

                if contains_ruby(title):
                    job_urls.append(href)

        job_urls = list(set(job_urls))
        log("SEARCH", f"Job URLs found: {len(job_urls)}")

        job_tasks = [process_job(session, u) for u in job_urls[:50]]
        results = await asyncio.gather(*job_tasks)

        for r in results:
            if r:
                jobs.append(r)

    return jobs

# ---------------- JOB PROCESSING ----------------

async def process_job(session, url):
    if url in CACHE:
        return None

    CACHE.add(url)

    html = await fetch(session, url)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    title = soup.find("h1").get_text(strip=True) if soup.find("h1") else None
    company = soup.find("h2").get_text(strip=True) if soup.find("h2") else urlparse(url).netloc

    if not title:
        return None

    if not contains_ruby(title):
        return None

    if is_excluded(title):
        return None

    return {
        "title": title,
        "link": url,
        "company": company
    }

# ---------------- MAIN ----------------

async def main():
    log("SYSTEM", "Starting Ruby Engine")

    store = load_companies()

    jobs = []
    jobs += fetch_rss_jobs()
    jobs += await fetch_search_jobs()

    # ---------------- DISCOVERY ----------------
    log("DISCOVERY", "Extracting companies")

    async with aiohttp.ClientSession() as session:
        tasks = [fetch(session, j["link"]) for j in jobs]
        pages = await asyncio.gather(*tasks)

        for html in pages:
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")

            links = soup.find_all("a", href=True)

            log("DISCOVERY", "Scanning links", len(links))

            for a in links:
                full_url = urljoin("https://", a["href"])
                discover_company(full_url, store)

    save_companies(store)

    # ---------------- DEDUPE ----------------
    seen = set()
    final = []

    for j in jobs:
        if j["link"] in seen:
            continue
        seen.add(j["link"])
        final.append(j)

    log("SYSTEM", f"Total jobs: {len(final)}")

    # ---------------- EMAIL ----------------

    body = f"🔥 RUBY ENGINE {datetime.now().strftime('%Y-%m-%d')}\n\n"

    for j in final[:10]:
        body += f"[{j['company']}] {j['title']}\n{j['link']}\n\n"

    msg = MIMEText(body)
    msg["Subject"] = "🔥 Ruby Job Engine"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(EMAIL_SENDER, EMAIL_PASSWORD)
            s.send_message(msg)
        log("EMAIL", "Sent successfully")
    except Exception as e:
        log("EMAIL", "Failed", str(e))

    log("SYSTEM", "Done")

# ---------------- RUN ----------------

if __name__ == "__main__":
    asyncio.run(main())
