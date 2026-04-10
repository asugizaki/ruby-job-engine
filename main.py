# ELITE RUBY JOB INTELLIGENCE SYSTEM (ASYNC + LOGGING + COMPANY MEMORY)
# --------------------------------------------------------------------

import asyncio
import aiohttp
import json
import re
import smtplib
import logging
import os
from email.mime.text import MIMEText
from datetime import datetime
from bs4 import BeautifulSoup
import feedparser
from playwright.async_api import async_playwright

# ---------------- LOGGING ----------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

def log(step, msg):
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
            return json.load(f)
    except:
        return {"greenhouse": [], "lever": [], "ashby": []}


def save_companies(data):
    with open(COMPANY_STORE_FILE, "w") as f:
        json.dump(data, f, indent=2)

def detect_platform(url):
    if "greenhouse" in url:
        return "greenhouse"
    if "lever" in url:
        return "lever"
    if "ashby" in url:
        return "ashby"
    return None


def discover_company(url, store):
    platform = detect_platform(url)
    if not platform:
        return

    parts = url.split("/")
    if len(parts) < 2:
        return

    company = parts[-1].strip()
    if not company:
        return

    if company not in store[platform]:
        store[platform].append(company)
        log("DISCOVERY", f"New company: {company} ({platform})")

# ---------------- HELPERS ----------------

def contains_ruby(text):
    t = text.lower()
    return any(k in t for k in RUBY_KEYWORDS)

def is_excluded(text):
    t = text.lower()
    return any(k in t for k in EXCLUDE_KEYWORDS)

def is_remote(text):
    return "remote" in text.lower()

# ---------------- ASYNC HTTP ----------------

async def fetch(session, url):
    try:
        async with session.get(url, timeout=20) as resp:
            return await resp.text()
    except Exception as e:
        log("HTTP", f"Failed {url}: {e}")
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

            log("RSS", f"Parsed {feed}")
        except Exception as e:
            log("RSS", f"Error {feed}: {e}")

    return jobs

# ---------------- PLAYWRIGHT FALLBACK ----------------

async def fetch_js(url):
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, timeout=30000)
            html = await page.content()
            await browser.close()
            return html
    except Exception as e:
        log("PLAYWRIGHT", f"Failed {url}: {e}")
        return None

# ---------------- SEARCH SCRAPER ----------------

async def fetch_search_jobs():
    jobs = []

    async with aiohttp.ClientSession() as session:

        tasks = []
        for url in SEARCH_PAGES:
            log("SEARCH", f"Scanning {url}")
            tasks.append(fetch(session, url))

        pages = await asyncio.gather(*tasks)

        job_urls = []

        for html in pages:
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")

            for a in soup.find_all("a", href=True):
                href = a["href"]
                title = a.get_text(strip=True)

                if len(title) < 5:
                    continue

                if href.startswith("/"):
                    href = "https://example.com" + href

                if contains_ruby(title):
                    job_urls.append(href)

        log("SEARCH", f"Found {len(job_urls)} candidate jobs")

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

    log("JOB", f"Fetching {url}")

    html = await fetch(session, url)
    if not html:
        return None

    if not contains_ruby(html):
        return None

    if is_excluded(html):
        return None

    if not is_remote(html):
        return None

    return {
        "title": "Ruby Job",
        "link": url,
        "company": "discovered"
    }

# ---------------- MAIN ----------------

async def main():
    log("SYSTEM", "Starting Ruby Job Engine")

    jobs = []

    # LOAD COMPANY GRAPH
    store = load_companies()

    # RSS
    jobs += fetch_rss_jobs()

    # SEARCH
    jobs += await fetch_search_jobs()

    # ---------------- COMPANY DISCOVERY ----------------
    log("DISCOVERY", "Extracting companies")

    async with aiohttp.ClientSession() as session:
        tasks = [fetch(session, j["link"]) for j in jobs]
        pages = await asyncio.gather(*tasks)

        for html in pages:
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")

            for a in soup.find_all("a", href=True):
                discover_company(a["href"], store)

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

    body = f"🔥 ASYNC RUBY ENGINE - {datetime.now().strftime('%Y-%m-%d')}\n\n"

    for j in final[:10]:
        body += f"[{j['company']}] {j['title']}\n{j['link']}\n\n"

    msg = MIMEText(body)
    msg["Subject"] = "🔥 Async Ruby Job Engine"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER

    log("DEBUG", f"Sender: {EMAIL_SENDER}")
    log("DEBUG", f"Receiver: {EMAIL_RECEIVER}")
    log("DEBUG", f"Password exists: {EMAIL_PASSWORD is not None}")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(EMAIL_SENDER, EMAIL_PASSWORD)
            s.send_message(msg)
        log("EMAIL", "Sent successfully")
    except Exception as e:
        log("EMAIL", f"Failed: {e}")

    log("SYSTEM", "Done")

# ---------------- RUN ----------------

if __name__ == "__main__":
    asyncio.run(main())
