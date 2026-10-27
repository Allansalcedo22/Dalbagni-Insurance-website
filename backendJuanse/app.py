import os
import re
import html
import hmac
import hashlib
import secrets
import time
import certifi
import smtplib
import threading
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from string import Template
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.parse import urlsplit
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
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
admin_password = os.getenv("ADMIN_PASSWORD")
# Where "Forgot password?" reset links are emailed (the site owner). Defaults to NOTIFICATION_EMAIL.
admin_email = os.getenv("ADMIN_EMAIL") or notification_email

if not mongo_uri:
    raise RuntimeError("MONGO_URI is missing from .env")

# Connect to MongoDB Atlas. MongoClient connects lazily, so ping to find out if it actually works.
db_client = MongoClient(mongo_uri, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=5000)
db = db_client["dalbagni_db"]
submissions_collection = db["submissions"]
analytics_collection = db["analytics"]
settings_collection = db["settings"]
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

def recent_request_count(bucket, window_seconds):
    """How many requests this IP has made to `bucket` in the window, without recording a new one."""
    key = (bucket, request.remote_addr)
    now = time.monotonic()
    with _request_log_lock:
        return sum(1 for t in _request_log[key] if now - t <= window_seconds)

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

def send_email(to_address, subject, html_body):
    """Sends an HTML email via Gmail SMTP. Returns True if it was sent."""
    if not email_user or not email_pass or not to_address:
        print("Email credentials or recipient missing in .env.")
        return False

    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = email_user
        msg['To'] = to_address
        # Plain text header: no HTML escaping, and no line breaks (prevents header injection)
        msg['Subject'] = clean_text(subject, 200)
        msg.attach(MIMEText(html_body, 'html'))

        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as server:
            server.starttls()
            server.login(email_user, email_pass)
            server.send_message(msg)

        print(f"Email sent with subject: {msg['Subject']}")
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False

def send_email_notification(subject, heading, sections):
    """Fills email_template.html and emails it to NOTIFICATION_EMAIL.

    sections is a list of (section title, rows), where each row is (label, value, link prefix or None).
    """
    try:
        template_path = os.path.join(os.path.dirname(__file__), 'email_template.html')
        with open(template_path, 'r', encoding='utf-8') as file:
            html_template = Template(file.read())

        sections_html = "\n".join(
            f'<div class="section-title">{html.escape(title)}</div>\n            {render_rows(rows)}'
            for title, rows in sections
        )
        rendered_html = html_template.safe_substitute(heading=html.escape(heading), sections=sections_html)
    except Exception as e:
        print(f"Failed to build email notification: {e}")
        return
    send_email(notification_email, subject, rendered_html)

def send_email_in_background(*args, target=send_email_notification):
    """Sends the email without making the visitor wait for Gmail."""
    threading.Thread(target=target, args=args, daemon=True).start()


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

# ---------- Admin login ----------
# The owner's password is stored hashed in MongoDB once they change it from admin.html,
# so nobody (including whoever runs the server) can read it.
# ADMIN_PASSWORD in .env is the recovery password: it always works, so if the owner forgets
# theirs, the site administrator can log in with it and set a new one.
# After 5 wrong passwords from one IP, that IP is locked out for 15 minutes.
ADMIN_MAX_FAILURES = 5
ADMIN_LOCKOUT_SECONDS = 900
ADMIN_PASSWORD_MIN_LENGTH = 12

def is_correct_admin_password(supplied):
    if hmac.compare_digest(supplied.encode(), admin_password.encode()):
        return True
    try:
        saved = settings_collection.find_one({"_id": "admin_password"})
    except Exception as e:
        print(f"Error reading admin password: {e}")
        return False
    return bool(saved) and check_password_hash(saved["hash"], supplied)

def new_admin_password_error(new_password):
    """Returns why new_password can't be used, or None if it's fine."""
    if not isinstance(new_password, str) or not ADMIN_PASSWORD_MIN_LENGTH <= len(new_password) <= 128:
        return f"New password must be {ADMIN_PASSWORD_MIN_LENGTH} to 128 characters."
    if new_password != new_password.strip():
        return "New password can't start or end with a space."
    if admin_password and hmac.compare_digest(new_password.encode(), admin_password.encode()):
        return "Please choose a different password."
    return None

def save_admin_password(new_password):
    settings_collection.update_one(
        {"_id": "admin_password"},
        {"$set": {"hash": generate_password_hash(new_password), "updatedAt": datetime.now(timezone.utc)}},
        upsert=True
    )

def check_admin_auth():
    """Returns (supplied password, None) if the request has a correct admin password,
    otherwise (None, error response)."""
    if not admin_password:
        return None, (jsonify({"success": False, "message": "Admin access is not configured."}), 503)
    if recent_request_count("admin-failure", ADMIN_LOCKOUT_SECONDS) >= ADMIN_MAX_FAILURES:
        return None, (jsonify({"success": False, "message": "Too many wrong passwords. Try again in 15 minutes."}), 429)

    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not supplied or not is_correct_admin_password(supplied):
        rate_limited("admin-failure", ADMIN_MAX_FAILURES, ADMIN_LOCKOUT_SECONDS)  # records the failure
        return None, (jsonify({"success": False, "message": "Incorrect password."}), 401)
    return supplied, None

# Requires the header "Authorization: Bearer <current password>" and JSON {"newPassword": "..."}.
@app.route('/api/admin/change-password', methods=['POST'])
def change_admin_password():
    current, error = check_admin_auth()
    if error:
        return error

    new_password = (request.get_json(silent=True) or {}).get("newPassword")
    if new_password == current:
        return jsonify({"success": False, "message": "New password must be different from the current one."}), 400
    password_error = new_admin_password_error(new_password)
    if password_error:
        return jsonify({"success": False, "message": password_error}), 400

    try:
        save_admin_password(new_password)
        return jsonify({"success": True, "message": "Password updated."}), 200
    except Exception as e:
        print(f"Error saving admin password: {e}")
        return jsonify({"success": False, "message": "Could not save the new password."}), 500

# ---------- Forgot password ----------
# "Forgot password?" emails the owner a one-time link that expires after 30 minutes.
# Only a hash of the link's token is stored, and the link only ever points at one of ALLOWED_ORIGINS.
RESET_LINK_MINUTES = 30
RESET_PATH_RE = re.compile(r"^/[A-Za-z0-9_\-./]*$")

def hash_reset_token(token):
    return hashlib.sha256(token.encode()).hexdigest()

def build_reset_link(page_url, token):
    """Returns page_url?reset=token if page_url is on an allowed origin, else None."""
    try:
        parts = urlsplit(page_url)
    except ValueError:
        return None
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in allowed_origins or not RESET_PATH_RE.match(parts.path or "/"):
        return None
    return f"{origin}{parts.path}?reset={token}"

def send_reset_email(link):
    safe_link = html.escape(link, quote=True)
    body = f"""<!DOCTYPE html>
<html><body style="font-family: Helvetica, Arial, sans-serif; background: #f4f6f9; margin: 0; padding: 20px;">
  <div style="max-width: 520px; margin: 0 auto; background: #ffffff; border-radius: 8px; overflow: hidden;">
    <div style="background: #0b2d52; color: #ffffff; padding: 24px; text-align: center;">
      <h2 style="margin: 0; font-size: 20px;">Reset Your Admin Password</h2>
    </div>
    <div style="padding: 28px; color: #333333; font-size: 15px; line-height: 1.6;">
      <p>Someone asked to reset the password for the Dalbagni Health admin dashboard.</p>
      <p style="text-align: center; margin: 28px 0;">
        <a href="{safe_link}" style="background: #f4c430; color: #0b2d52; padding: 14px 28px; border-radius: 50px; text-decoration: none; font-weight: bold;">Choose a New Password</a>
      </p>
      <p>This link works once and expires in {RESET_LINK_MINUTES} minutes.</p>
      <p style="color: #6c757d; font-size: 13px;">If you didn't ask for this, you can ignore this email. Your password won't change.</p>
    </div>
  </div>
</body></html>"""
    send_email(admin_email, "Reset your Dalbagni Health admin password", body)

# Requires JSON {"pageUrl": "<the admin page's address>"}; the link in the email points there.
@app.route('/api/admin/forgot-password', methods=['POST'])
def forgot_admin_password():
    if rate_limited("admin-forgot", limit=3, window_seconds=3600):
        return jsonify({"success": False, "message": "Too many reset requests. Please try again in an hour."}), 429
    if not (email_user and email_pass and admin_email):
        return jsonify({"success": False, "message": "Password reset by email isn't set up yet."}), 503

    token = secrets.token_urlsafe(32)
    link = build_reset_link((request.get_json(silent=True) or {}).get("pageUrl") or "", token)
    if not link:
        return jsonify({"success": False, "message": "Invalid request."}), 400

    try:
        # A new request replaces any earlier link
        settings_collection.update_one(
            {"_id": "admin_reset"},
            {"$set": {"tokenHash": hash_reset_token(token), "expiresAt": time.time() + RESET_LINK_MINUTES * 60}},
            upsert=True
        )
    except Exception as e:
        print(f"Error saving reset token: {e}")
        return jsonify({"success": False, "message": "Could not send the reset email. Please try again."}), 500

    send_email_in_background(link, target=send_reset_email)
    return jsonify({"success": True, "message": "We've emailed a reset link to the owner's email address."}), 200

# Requires JSON {"token": "<from the emailed link>", "newPassword": "..."}.
@app.route('/api/admin/reset-password', methods=['POST'])
def reset_admin_password():
    if rate_limited("admin-reset", limit=10, window_seconds=900):
        return too_many_requests()

    data = request.get_json(silent=True) or {}
    token = data.get("token")
    new_password = data.get("newPassword")
    if not isinstance(token, str) or not token or len(token) > 100:
        return jsonify({"success": False, "message": "This reset link is invalid or has expired."}), 400
    password_error = new_admin_password_error(new_password)
    if password_error:
        return jsonify({"success": False, "message": password_error}), 400

    try:
        # Deleting while matching makes the link single-use, even if clicked twice at once
        reset = settings_collection.find_one_and_delete({"_id": "admin_reset", "tokenHash": hash_reset_token(token)})
        if not reset or reset.get("expiresAt", 0) < time.time():
            return jsonify({"success": False, "message": "This reset link is invalid or has expired."}), 400
        save_admin_password(new_password)
        return jsonify({"success": True, "message": "Password updated."}), 200
    except Exception as e:
        print(f"Error resetting admin password: {e}")
        return jsonify({"success": False, "message": "Could not save the new password."}), 500

# Dashboard statistics for admin.html. Requires the header "Authorization: Bearer <password>".
@app.route('/api/admin/stats', methods=['GET'])
def get_dashboard_stats():
    _, error = check_admin_auth()
    if error:
        return error

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
