import os
import json
import imaplib
import email
from email.header import decode_header
from email.utils import parseaddr 
from google import genai
import asyncio
import time
import html
from html.parser import HTMLParser
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

# --- INITIALIZE CLIENTS ---
client = genai.Client(api_key=os.getenv("GCP_AI_STUDIO_KEY") or os.getenv("GEMINIS_API_KEY") or os.getenv("GEMINI_API_KEY"))
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")

WHITELIST_FILE = "whitelist.json"

class HTMLFilter(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.links = []
    def handle_data(self, d):
        self.text.append(d)
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            for attr, value in attrs:
                if attr == 'href' and value:
                    self.links.append(value)

def load_whitelist():
    """Loads the whitelist rules from local storage."""
    if not os.path.exists(WHITELIST_FILE):
        return {"senders": [], "domains": [], "keywords": []}
    with open(WHITELIST_FILE, "r") as f:
        return json.load(f)

def save_whitelist(data):
    """Saves updated whitelist rules back to storage."""
    with open(WHITELIST_FILE, "w") as f:
        json.dump(data, f, indent=4)

def matches_whitelist(sender, subject, body):
    """Evaluates an email against Senders, Domains, and Keywords using safe parsing."""
    whitelist = load_whitelist()
    
    _, extracted_email = parseaddr(sender)
    sender_lower = sender.lower()
    email_lower = extracted_email.lower()
    full_text = (subject + " " + body).lower()
    
    for s in whitelist.get("senders", []):
        if s.lower() in sender_lower or s.lower() in email_lower:
            return True, f"Sender Match: {s}"
            
    for d in whitelist.get("domains", []):
        target_domain = d.lower().strip("@")
        if email_lower.endswith(f"@{target_domain}") or f"@{target_domain}" in sender_lower:
            return True, f"Domain Match: @{d}"
            
    for kw in whitelist.get("keywords", []):
        if kw.lower() in full_text:
            return True, f"Keyword: '{kw}'"
            
    return False, None

def fetch_and_process_inbox(since_date=None):
    """Connects via IMAP, checks unread messages with date filtering, extracts text & links safely."""
    host = str(os.getenv("EMAIL_HOST", "imap.gmail.com")).encode("ascii", "ignore").decode("ascii")
    user = str(os.getenv("EMAIL_USER", "")).encode("ascii", "ignore").decode("ascii")
    password = str(os.getenv("EMAIL_PASS", "")).encode("ascii", "ignore").decode("ascii")
    
    try:
        mail = imaplib.IMAP4_SSL(host)
        mail.login(user, password)   
        mail.select("inbox")      

        search_query = f'(UNSEEN SINCE "{since_date}")' if since_date else 'UNSEEN'
        status, messages = mail.search(None, search_query)
        
        if status != "OK" or not messages[0]:
            status, messages = mail.search(None, 'UNSEEN')
        
        unmatched_emails = []
        instant_alerts = []
        
        if status == "OK" and messages[0]:
            msg_nums = messages[0].split()
            for num in msg_nums[:10]: # Capped strictly to 10 to conserve free-tier tokens
                res, msg_data = mail.fetch(num, '(RFC822)')
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        
                        raw_subject = msg.get("Subject", "No Subject")
                        subject_header = decode_header(raw_subject)[0]
                        subject = subject_header[0]
                        if isinstance(subject, bytes):
                            encoding = subject_header[1] or "utf-8"
                            try:
                                subject = subject.decode(encoding, errors="ignore")
                            except LookupError:
                                subject = subject.decode("utf-8", errors="ignore")
                        
                        subject = str(subject).replace("\xa0", " ").strip()
                        sender = str(msg.get("From", "")).replace("\xa0", " ").strip()
                        
                        body = ""
                        extracted_links = []
                        html_body = ""
                        
                        if msg.is_multipart():
                            for part in msg.walk():
                                content_type = part.get_content_type()
                                payload = part.get_payload(decode=True)
                                if payload:
                                    decoded_payload = payload.decode(errors="ignore")
                                    if content_type == "text/plain":
                                        body += decoded_payload + "\n"
                                    elif content_type == "text/html":
                                        html_body += decoded_payload + "\n"
                        else:
                            payload = msg.get_payload(decode=True)
                            if payload:
                                decoded_payload = payload.decode(errors="ignore")
                                if msg.get_content_type() == "text/html":
                                    html_body = decoded_payload
                                else:
                                    body = decoded_payload
                        
                        if html_body:
                            parser = HTMLFilter()
                            parser.feed(html_body)
                            body += " " + " ".join("".join(parser.text).split())
                            extracted_links = parser.links
                        
                        is_whitelisted, reason = matches_whitelist(sender, subject, body)
                        
                        if is_whitelisted:
                            instant_alerts.append({
                                "sender": sender,
                                "subject": subject,
                                "body": body[:800], 
                                "links": extracted_links[:5],
                                "reason": reason
                            })
                        else:
                            unmatched_emails.append({
                                "sender": sender,
                                "subject": subject,
                                "snippet": body[:200],
                                "links": extracted_links[:3]
                            })
                            
        mail.logout()
        return instant_alerts, unmatched_emails
    except Exception as e:
        print(f"IMAP Error: {e}")
        return [], []

async def call_gemini_with_retry(prompt, max_retries=3):
    """Wrapper with exponential backoff to automatically handle 429 rate limit spikes."""
    delay = 10
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model='gemini-3.5-flash',
                contents=prompt,
            )
            return response.text
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                if attempt < max_retries - 1:
                    print(f"Rate limited (429). Retrying in {delay} seconds... (Attempt {attempt+1}/{max_retries})")
                    await asyncio.sleep(delay)
                    delay *= 2 
                    continue
            print(f"Gemini API Error: {e}")
            return None
    return None
    
async def ai_summarize_whitelist_matches(instant_alerts):
    """Asks Gemini 3.5 Flash to organize and summarize whitelist hits with built-in retry."""
    if not instant_alerts:
        return None
        
    prompt = f"""
    You are an executive chief of staff. Review the following batch of unread emails that matched the user's whitelist rules.
    Provide a comprehensive breakdown for each email including:
    1. A detailed summary explaining the core message, instructions, or updates.
    2. Any specific action links found in the data payload.
    
    Format cleanly with markdown emojis and bullet points:
    
    🚨 **Whitelist Updates Summary ({len(instant_alerts)})**
    
    • **[Sender / Subject]**
      * **Triggered By:** [Reason]
      * **Summary:** [Detailed 2-3 sentence overview]
      * **Action Link:** [URL or "None available"]
    
    Emails to process:
    {instant_alerts}
    """
    return await call_gemini_with_retry(prompt)

async def ai_select_wildcards(unmatched_emails):
    """Asks Gemini 3.5 Flash to pick top surprise important emails with built-in retry."""
    if not unmatched_emails:
        return None
        
    prompt = f"""
    You are an executive chief of staff. Review the following unread emails that did not match the user's whitelist.
    Filter out standard spam and marketing ads, but keep important platform notifications, development updates, and educational notices. Select the **absolute best 1 or 2 emails** that the user needs to see.
    If none are important, return "NONE".
    Otherwise, format them cleanly like this:
    
    🎯 **AI Wildcard Pick**
    • **From:** [Sender]
    • **Subject:** [Subject]
    • **Why it matters:** [1-2 sentence detailed summary]
    • **Action Link:** [URL or "None available"]
    
    Emails to review:
    {unmatched_emails}
    """
    return await call_gemini_with_retry(prompt)

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage format: `/add sender client@domain.com` or `/add keyword \"project update\"`", parse_mode="Markdown")
        return
        
    rule_type = args[0].lower()
    value = " ".join(args[1:]).strip('"')
    whitelist = load_whitelist()
    
    if rule_type == "sender":
        whitelist["senders"].append(value)
    elif rule_type == "domain":
        whitelist["domains"].append(value)
    elif rule_type == "keyword":
        whitelist["keywords"].append(value.lower())
    else:
        await update.message.reply_text("Invalid type. Use 'sender', 'domain', or 'keyword'.")
        return
        
    save_whitelist(whitelist)
    await update.message.reply_text(f"✅ Successfully added `{value}` as a new whitelist {rule_type}!", parse_mode="Markdown")

async def run_scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    filter_type = args[0].lower() if args else "week"
    
    now = datetime.now()
    if filter_type == "today":
        since_date = (now - timedelta(days=1)).strftime("%d-%b-%Y")
        label = "Today"
    elif filter_type == "month":
        since_date = (now - timedelta(days=30)).strftime("%d-%b-%Y")
        label = "Past 30 Days"
    else:
        since_date = (now - timedelta(days=7)).strftime("%d-%b-%Y")
        label = "Past 7 Days"
        
    await update.message.reply_text(f"🔍 Scanning unread inbox for **{label}**...")
    
    instant_alerts, unmatched = await asyncio.to_thread(fetch_and_process_inbox, since_date)
    
    summary_output = await ai_summarize_whitelist_matches(instant_alerts)
    
    # Built-in pause to keep request spacing safe for free tier limits
    await asyncio.sleep(5)
    
    wildcard_output = await ai_select_wildcards(unmatched[:3])
    
    if summary_output:
        if len(summary_output) > 4000:
            chunks = [summary_output[i:i+4000] for i in range(0, len(summary_output), 4000)]
            for chunk in chunks:
                await context.bot.send_message(chat_id=CHAT_ID, text=chunk)
        else:
            await context.bot.send_message(chat_id=CHAT_ID, text=summary_output)
        
    if wildcard_output and "NONE" not in wildcard_output:
        await context.bot.send_message(chat_id=CHAT_ID, text=wildcard_output)
        
    if not instant_alerts and (not wildcard_output or "NONE" in wildcard_output):
        await context.bot.send_message(chat_id=CHAT_ID, text="Inbox checked. No critical hits or wildcards found.")

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN is missing from your .env file!")
        exit(1)

    print("🤖 Bot is starting up and connecting to Telegram...")
    
    while True:
        try:
            application = ApplicationBuilder().token(BOT_TOKEN).build()
            
            application.add_handler(CommandHandler("add", cmd_add))
            application.add_handler(CommandHandler("scan", run_scan_command))
            
            print("🤖 Bot is now live and listening for Telegram commands...")
            
            # Polling configuration optimized with timeouts to handle network hiccups smoothly
            application.run_polling(
                drop_pending_updates=True,
                read_timeout=30,
                write_timeout=30,
                connect_timeout=30,
                pool_timeout=30
            )
            
        except Exception as e:
            print(f"Network drop detected: {e}. Reconnecting smoothly in 5 seconds...")
            import time
            time.sleep(5)