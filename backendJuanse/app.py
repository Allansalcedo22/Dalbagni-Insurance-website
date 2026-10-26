import os
import re
import html
import hmac
import time
import certifi
import smtplib
import threading
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from string import Template
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
# Reject request bodies over 16 KB (the forms send well under 4 KB)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024

# Only these sites may call the API from a browser. Set ALLOWED_ORIGINS in .env
# to a comma-separated list, e.g. https://dalbagniinsurance.com,https://www.dalbagniinsurance.com
default_origins = "http://127.0.0.1:5502,http://localhost:5502,http://127.0.0.1:5500,http://localhost:5500,http://localhost:8765"
allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", default_origins).split(",") if o.strip()]
CORS(app, origins=allowed_origins)

mongo_uri = os.getenv("MONGO_URI")
email_user = os.getenv("EMAIL_USER")
email_pass = os.getenv("EMAIL_PASS")
notification_email = os.getenv("NOTIFICATION_EMAIL")
admin_token = os.getenv("ADMIN_TOKEN")

if not mongo_uri:
    raise RuntimeError("MONGO_URI is missing from .env")

# Connect to MongoDB Atlas. MongoClient connects lazily, so ping to find out if it actually works.
db_client = MongoClient(mongo_uri, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=5000)
db = db_client["dalbagni_db"]
submissions_collection = db["submissions"]
analytics_collection = db["analytics"]
try:
    db_client.admin.command("ping")
    print("Successfully connected to MongoDB Atlas!")
except Exception as e:
    print(f"WARNING: MongoDB is not reachable yet: {e}")


# ---------- Rate limiting (in-memory, per IP) ----------
# Resets when the server restarts and isn't shared between multiple server processes,
# which is fine for a single small Flask server.

_request_log = defaultdict(deque)
_request_log_lock = threading.Lock()

def rate_limited(bucket, limit, window_seconds):
    """Returns True if this IP has made more than `limit` requests to `bucket` in the window."""
    key = (bucket, request.remote_addr)
    now = time.monotonic()
    with _request_log_lock:
        timestamps = _request_log[key]
        while timestamps and now - timestamps[0] > window_seconds:
            timestamps.popleft()
        if len(timestamps) >= limit:
            return True
        timestamps.append(now)
        return False

def too_many_requests():
    return jsonify({"success": False, "message": "Too many requests. Please try again in a few minutes."}), 429


# ---------- Validation ----------

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[a-zA-Z]{2,}$")
DOB_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
ZIP_RE = re.compile(r"^\d{5}$")

def clean_text(value, max_length):
    """Trims a value to a single-line string of at most max_length characters."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"[\r\n\t]+", " ", value).strip()[:max_length]

def clean_multiline(value, max_length):
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_length]

def is_valid_email(value):
    return len(value) <= 254 and bool(EMAIL_RE.match(value))

def normalize_phone(value):
    """Returns the 10-digit US number, or None if it isn't a valid one."""
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    # US area codes and exchanges can't start with 0 or 1
    if re.fullmatch(r"[2-9]\d{2}[2-9]\d{6}", digits):
        return digits
    return None

def format_phone(digits):
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"

def is_valid_dob(value):
    match = DOB_RE.match(value)
    if not match:
        return False
    month, day, year = (int(part) for part in match.groups())
    try:
        birth_date = date(year, month, day)
    except ValueError:
        return False
    today = date.today()
    return today.replace(year=today.year - 120) <= birth_date <= today

def is_spam(data):
    """The forms include a hidden 'website' field that people never see; bots tend to fill it in."""
    return bool(data.get("website"))

def validate_contact_fields(data):
    """Checks the name/email/phone fields both forms share. Returns (cleaned, errors)."""
    errors = []
    name = clean_text(data.get("name"), 100)
    email = clean_text(data.get("email"), 254)
    phone_digits = normalize_phone(clean_text(data.get("phone"), 30))

    if not name:
        errors.append("Name is required.")
    if not is_valid_email(email):
        errors.append("A valid email address is required.")
    if not phone_digits:
        errors.append("A valid 10-digit US phone number is required.")

    cleaned = {"name": name, "email": email, "phone": format_phone(phone_digits) if phone_digits else ""}
    return cleaned, errors

def submission_metadata():
    return {
        "submittedAt": datetime.now(timezone.utc),
        "ipAddress": request.remote_addr,
        "userAgent": clean_text(request.headers.get("User-Agent"), 300),
    }


# ---------- Email ----------

def render_rows(rows):
    """Builds the HTML for each label/value row. Values are escaped here, once."""
    parts = []
    for label, value, link_prefix in rows:
        safe_value = html.escape(value or "N/A")
        if link_prefix and value:
            safe_value = f'<a href="{link_prefix}{html.escape(value, quote=True)}">{safe_value}</a>'
        parts.append(
            f'<div class="data-row"><span class="data-label">{html.escape(label)}:</span> '
            f'<span class="data-value">{safe_value}</span></div>'
        )
    return "\n            ".join(parts)

def send_email_notification(subject, heading, sections):
    """Fills email_template.html and sends it via Gmail SMTP.

    sections is a list of (section title, rows), where each row is (label, value, link prefix or None).
    """
    if not email_user or not email_pass or not notification_email:
        print("Email credentials or notification recipient missing in .env.")
        return

    try:
        template_path = os.path.join(os.path.dirname(__file__), 'email_template.html')
        with open(template_path, 'r', encoding='utf-8') as file:
            html_template = Template(file.read())

        sections_html = "\n".join(
            f'<div class="section-title">{html.escape(title)}</div>\n            {render_rows(rows)}'
            for title, rows in sections
        )
        rendered_html = html_template.safe_substitute(heading=html.escape(heading), sections=sections_html)

        msg = MIMEMultipart('alternative')
        msg['From'] = email_user
        msg['To'] = notification_email
        # Plain text header: no HTML escaping, and no line breaks (prevents header injection)
        msg['Subject'] = clean_text(subject, 200)
        msg.attach(MIMEText(rendered_html, 'html'))

        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as server:
            server.starttls()
            server.login(email_user, email_pass)
            server.send_message(msg)

        print(f"Formatted HTML email sent with subject: {msg['Subject']}")
    except Exception as e:
        print(f"Failed to send email notification: {e}")

def send_email_in_background(*args):
    """Sends the email without making the visitor wait for Gmail."""
    threading.Thread(target=send_email_notification, args=args, daemon=True).start()


# ---------- Routes ----------

# Endpoint 1: Quote Form (quote.html)
@app.route('/api/submit-form', methods=['POST'])
def submit_form():
    if rate_limited("submit", limit=5, window_seconds=600):
        return too_many_requests()

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "No data provided"}), 400
    if is_spam(data):
        return jsonify({"success": True, "message": "Form submitted successfully!"}), 200

    contact, errors = validate_contact_fields(data)
    zip_code = clean_text(data.get("zipCode"), 5)
    dob = clean_text(data.get("dob"), 10)
    consent_text = clean_multiline(data.get("consentText"), 2000)

    if not ZIP_RE.match(zip_code):
        errors.append("A valid 5-digit ZIP code is required.")
    if not is_valid_dob(dob):
        errors.append("A valid date of birth (MM/DD/YYYY) is required.")
    if data.get("tcpaConsent") is not True or not consent_text:
        errors.append("You must agree to be contacted before submitting.")
    if errors:
        return jsonify({"success": False, "message": " ".join(errors)}), 400

    submission = {
        "formType": "Quote Request",
        **contact,
        "zipCode": zip_code,
        "dob": dob,
        "income": clean_text(data.get("income"), 50) or "N/A",
        "referralSource": clean_text(data.get("referralSource"), 100) or "N/A",
        "details": clean_multiline(data.get("details"), 2000) or "None",
        # Proof of TCPA consent: what they agreed to, and when/where from
        "consent": {"agreed": True, "text": consent_text, **submission_metadata()},
        **submission_metadata(),
    }

    try:
        submissions_collection.insert_one(submission)
    except Exception as e:
        print(f"Error saving quote submission: {e}")
        return jsonify({"success": False, "message": "We couldn't save your request. Please try again or call us."}), 500

    send_email_in_background(
        f"📋 NEW QUOTE REQUEST: {submission['name']}",
        "New Quote Request Received",
        [
            ("Client Profile", [
                ("Full Name", submission["name"], None),
                ("Email Address", submission["email"], "mailto:"),
                ("Phone Number", submission["phone"], "tel:"),
            ]),
            ("Demographics & Coverage", [
                ("ZIP Code", submission["zipCode"], None),
                ("Date of Birth", submission["dob"], None),
                ("Household Income", submission["income"], None),
                ("Referral Source", submission["referralSource"], None),
            ]),
            ("Additional Client Notes", [("Notes", submission["details"], None)]),
        ],
    )

    return jsonify({"success": True, "message": "Form submitted successfully!"}), 200

# Endpoint 2: Contact Us Form (index.html)
SERVICE_LABELS = {
    "individual": "Individual & Family Health Plans",
    "business": "Business / Group Health Insurance",
    "other": "Other Resources & Assistance",
}

@app.route('/api/submit-contact', methods=['POST'])
def handle_contact_submission():
    if rate_limited("submit", limit=5, window_seconds=600):
        return too_many_requests()

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "No data provided"}), 400
    if is_spam(data):
        return jsonify({"success": True, "message": "Message sent."}), 200

    contact, errors = validate_contact_fields(data)
    service = data.get("service")
    if service not in SERVICE_LABELS:
        errors.append("Please choose what you need assistance with.")
    if errors:
        return jsonify({"success": False, "message": " ".join(errors)}), 400

    submission = {
        "formType": "Contact Inquiry",
        **contact,
        "service": SERVICE_LABELS[service],
        **submission_metadata(),
    }

    try:
        submissions_collection.insert_one(submission)
    except Exception as e:
        print(f"Error saving contact submission: {e}")
        return jsonify({"success": False, "message": "We couldn't send your message. Please try again or call us."}), 500

    send_email_in_background(
        f"💬 GENERAL INQUIRY: {submission['name']} needs assistance",
        "New Contact Inquiry Received",
        [
            ("Client Profile", [
                ("Full Name", submission["name"], None),
                ("Email Address", submission["email"], "mailto:"),
                ("Phone Number", submission["phone"], "tel:"),
            ]),
            ("Request", [("Needs Help With", submission["service"], None)]),
        ],
    )

    return jsonify({
        "success": True,
        "message": "Message sent. Dalbagni Health will get back to you in the upcoming days."
    }), 200

@app.route('/api/track-view', methods=['POST'])
def track_view():
    # One counted view per IP per 30 minutes, so refreshing or scripting can't inflate the number
    if rate_limited("track-view", limit=1, window_seconds=1800):
        return jsonify({"success": True}), 200
    try:
        # Increment total pageviews using MongoDB atomic operators
        analytics_collection.update_one(
            {"_id": "site_stats"},
            {"$inc": {"total_views": 1}},
            upsert=True
        )
        return jsonify({"success": True}), 200
    except Exception as e:
        print(f"Error tracking view: {e}")
        return jsonify({"success": False}), 500

# Dashboard statistics. Requires the header "Authorization: Bearer <ADMIN_TOKEN from .env>".
@app.route('/api/admin/stats', methods=['GET'])
def get_dashboard_stats():
    if not admin_token:
        return jsonify({"success": False, "message": "Admin access is not configured."}), 503
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied, admin_token):
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    try:
        # Fetch pageviews
        stats = analytics_collection.find_one({"_id": "site_stats"}) or {"total_views": 0}

        # Count conversions directly from MongoDB submissions
        contact_conversions = submissions_collection.count_documents({"formType": "Contact Inquiry"})
        quote_conversions = submissions_collection.count_documents({"formType": "Quote Request"})
        total_conversions = contact_conversions + quote_conversions

        return jsonify({
            "success": True,
            "total_pageviews": stats.get("total_views", 0),
            "contact_inquiries": contact_conversions,
            "quote_requests": quote_conversions,
            "total_conversions": total_conversions
        }), 200
    except Exception as e:
        print(f"Error fetching stats: {e}")
        return jsonify({"success": False, "message": "Could not load stats."}), 500

@app.errorhandler(413)
def payload_too_large(_error):
    return jsonify({"success": False, "message": "Submission is too large."}), 413


if __name__ == '__main__':
    # Debug mode lets anyone who can reach the server run code on it, so it's opt-in
    # (FLASK_DEBUG=1 in .env) and the server only listens on this machine by default.
    debug = os.getenv("FLASK_DEBUG") == "1"
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    app.run(host=host, port=port, debug=debug)
