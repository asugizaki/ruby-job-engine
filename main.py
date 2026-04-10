import os
import json
import asyncio
import aiohttp
import logging
import re
from collections import defaultdict
from urllib.parse import urlparse, urljoin

# =========================
# LOGGING
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# =========================
# CONFIG
# =========================

COMPANIES_FILE = "companies.json"

INCLUDE_TITLE = ["engineer", "developer"]
MUST_HAVE_SENIOR = ["senior", "sr"]

EXCLUDE = [
    "staff", "principal", "vp", "director",
    "head", "lead", "manager", "architect",
    "intern", "junior"
]

CANADA_KEYWORDS = ["canada", "remote", "ca"]

CACHE = set()

# =========================
# FILE
# =========================

def load_companies():
    if not os.path.exists(COMPANIES_FILE):
        return []
    return json.load(open(COMPANIES_FILE))

def save_companies(c):
    json.dump(c, open(COMPANIES_FILE, "w"), indent=2)

# =========================
# SALARY (FIXED PROPERLY)
# =========================

def extract_salary(text):
    if not text:
        return None

    # normalize unicode dashes
    t = text.replace("—", "-").replace("–", "-").replace(",", "")

    patterns = [
        r"\$\d{5,6}\s*-\s*\$\d{5,6}",
        r"\$\d{2,3}k\s*-\s*\$\d{2,3}k",
        r"\$\d{5,6}",
    ]

    for p in patterns:
        m = re.search(p, t)
        if m:
            return m.group(0)

    return None

async def fetch_salary_from_page(session, url):
    try:
        async with session.get(url, timeout=20) as r:
            html = await r.text()
            return extract_salary(html)
    except:
        return None

# =========================
# FILTERING
# =========================

def is_valid(title, text):
    t = title.lower()

    if not any(k in t for k in INCLUDE_TITLE):
        return False, "NOT_ENGINEER"

    if not any(k in t for k in MUST_HAVE_SENIOR):
        return False, "NOT_SENIOR"

    if any(k in t for k in EXCLUDE):
        return False, "EXCLUDED_ROLE"

    full = (title + " " + text).lower()

    if not any(k in full for k in CANADA_KEYWORDS):
        return False, "NOT_CANADA"

    return True, "OK"

def log_result(status, job):
    logging.info(f"[{status}] {job['title']} | {job['url']}")

# =========================
# COMPANY DETECTION
# =========================

def detect_company(url):
    try:
        domain = urlparse(url).netloc
        path = urlparse(url).path.strip("/").split("/")

        if "greenhouse" in domain:
            return {"platform": "greenhouse", "slug": path[0]}

        if "lever" in domain:
            return {"platform": "lever", "slug": path[0]}

        if "ashby" in domain:
            return {"platform": "ashby", "slug": path[0]}

        if "workable" in domain:
            return {"platform": "workable", "slug": path[0]}

    except:
        pass

    return None

def update_companies(existing, jobs):
    seen = set((c["platform"], c["slug"]) for c in existing)

    for j in jobs:
        c = detect_company(j["url"])
        if not c:
            continue

        key = (c["platform"], c["slug"])

        if key not in seen:
            logging.info(f"[NEW COMPANY] {c}")
            existing.append(c)
            seen.add(key)

    return existing

# =========================
# GREENHOUSE
# =========================

async def fetch_greenhouse(session, slug):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    logging.info(f"[GREENHOUSE] {slug}")

    try:
        async with session.get(url) as r:
            if r.status != 200:
                logging.warning(f"[GH {slug}] {r.status}")
                return []
            data = await r.json()
    except:
        return []

    jobs = []

    for j in data.get("jobs", []):
        title = j.get("title", "")
        desc = j.get("content", "")
        link = j.get("absolute_url")

        job = {
            "company": slug,
            "title": title,
            "url": link,
            "salary": extract_salary(desc),
        }

        valid, reason = is_valid(title, desc)

        if not valid:
            log_result(f"REJECT-{reason}", job)
            continue

        if not job["salary"]:
            job["salary"] = await fetch_salary_from_page(session, link)

        job["salary"] = job["salary"] or "Not specified"

        log_result("ACCEPT", job)
        jobs.append(job)

    return jobs

# =========================
# LEVER
# =========================

async def fetch_lever(session, slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    logging.info(f"[LEVER] {slug}")

    try:
        async with session.get(url) as r:
            data = await r.json()
    except:
        return []

    jobs = []

    for j in data:
        title = j.get("text", "")
        desc = j.get("description", "")
        link = j.get("hostedUrl")

        job = {
            "company": slug,
            "title": title,
            "url": link,
            "salary": extract_salary(desc),
        }

        valid, reason = is_valid(title, desc)

        if not valid:
            log_result(f"REJECT-{reason}", job)
            continue

        if not job["salary"]:
            job["salary"] = await fetch_salary_from_page(session, link)

        job["salary"] = job["salary"] or "Not specified"

        log_result("ACCEPT", job)
        jobs.append(job)

    return jobs

# =========================
# HIMALAYAS (CANADA FIX)
# =========================

async def fetch_himalayas(session):
    base = "https://himalayas.app/jobs/countries/canada?q=ruby"
    logging.info("[HIMALAYAS] start")

    jobs = []

    for page in range(1, 4):  # paginate
        url = base + f"&page={page}"
        logging.info(f"[HIMALAYAS] page {page}")

        try:
            async with session.get(url) as r:
                html = await r.text()
        except:
            continue

        matches = re.findall(
            r'<a[^>]+href="(/companies/[^"]+/jobs/[^"]+)"[^>]*>(.*?)</a>',
            html,
            re.DOTALL
        )

        for link, raw_title in matches:
            title = re.sub("<.*?>", "", raw_title).strip()
            full_url = urljoin("https://himalayas.app", link)

            if full_url in CACHE:
                continue
            CACHE.add(full_url)

            job = {
                "company": "himalayas",
                "title": title,
                "url": full_url,
                "salary": "Not specified"
            }

            valid, reason = is_valid(title, "")

            if not valid:
                log_result(f"REJECT-{reason}", job)
                continue

            log_result("ACCEPT", job)
            jobs.append(job)

    logging.info(f"[HIMALAYAS] total {len(jobs)}")
    return jobs

# =========================
# EMAIL
# =========================

def group_jobs(jobs):
    g = defaultdict(list)
    for j in jobs:
        g[j["company"]].append(j)
    return g

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

def send_email(body, count):
    import smtplib
    from email.mime.text import MIMEText

    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")
    receiver = os.getenv("EMAIL_RECEIVER")

    msg = MIMEText(body)
    msg["Subject"] = f"Senior Ruby Jobs ({count})"
    msg["From"] = sender
    msg["To"] = receiver

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(sender, password)
        s.sendmail(sender, receiver, msg.as_string())

    logging.info(f"[EMAIL] Sent {count}")

# =========================
# MAIN
# =========================

async def main():
    logging.info("[SYSTEM] Starting")

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

    jobs = [j for sub in results for j in sub]

    logging.info(f"[DONE] {len(jobs)} jobs")

    companies = update_companies(companies, jobs)
    save_companies(companies)

    grouped = group_jobs(jobs)
    email = format_email(grouped)

    send_email(email, len(jobs))

if __name__ == "__main__":
    asyncio.run(main())
