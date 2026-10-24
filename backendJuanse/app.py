import os
import html
import certifi
import smtplib
from string import Template
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

mongo_uri = os.getenv("MONGO_URI")
email_user = os.getenv("EMAIL_USER")
email_pass = os.getenv("EMAIL_PASS")
notification_email = os.getenv("NOTIFICATION_EMAIL")

# Connect to MongoDB Atlas
try:
    db_client = MongoClient(mongo_uri, tlsCAFile=certifi.where())
    db = db_client["dalbagni_db"]
    submissions_collection = db["submissions"]
    print("Successfully connected to MongoDB Atlas!")
except Exception as e:
    print(f"MongoDB connection error: {e}")

def sanitize(input_string):
    """Encodes standard HTML characters to prevent XSS issues."""
    if not isinstance(input_string, str):
        return ""
    return html.escape(input_string.strip())

def send_email_notification(data, subject_title=None):
    """Loads email_template.html and transmits it via Gmail SMTP with dynamic subjects."""
    if not email_user or not email_pass or not notification_email:
        print("Email credentials or notification recipient missing in .env.")
        return

    try:
        template_path = os.path.join(os.path.dirname(__file__), 'email_template.html')
        with open(template_path, 'r', encoding='utf-8') as file:
            html_template = Template(file.read())

        rendered_html = html_template.safe_substitute(
            name=sanitize(data.get('name', 'N/A')),
            email=sanitize(data.get('email', 'N/A')),
            phone=sanitize(data.get('phone', 'N/A')),
            zipCode=sanitize(data.get('zipCode', 'N/A')),
            dob=sanitize(data.get('dob', 'N/A')),
            income=sanitize(data.get('income', 'N/A')),
            referralSource=sanitize(data.get('referralSource', 'N/A')),
            details=sanitize(data.get('details', 'None'))
        )

        msg = MIMEMultipart('alternative')
        msg['From'] = email_user
        msg['To'] = notification_email
        
        # Set dynamic subject line (or default fallback if none provided)
        if not subject_title:
            subject_title = f"⚡ New Lead: {sanitize(data.get('name', 'Client'))}"

        msg['Subject'] = subject_title
        msg.attach(MIMEText(rendered_html, 'html'))

        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(email_user, email_pass)
            server.send_message(msg)

        print(f"Formatted HTML email sent with subject: {subject_title}")
    except Exception as e:
        print(f"Failed to send email notification: {e}")

# Endpoint 1: Quote Form (quote.html)
@app.route('/api/submit-form', methods=['POST'])
def submit_form():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "No data provided"}), 400

        submissions_collection.insert_one(data)

        # Custom subject line for Quote submissions
        quote_subject = f"📋 NEW QUOTE REQUEST: {sanitize(data.get('name', 'Client'))}"
        send_email_notification(data, subject_title=quote_subject)

        return jsonify({"success": True, "message": "Form submitted successfully!"}), 200
    except Exception as e:
        print(f"Error handling quote submission: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

# Endpoint 2: Contact Us Form (index.html)
@app.route('/api/submit-contact', methods=['POST'])
def handle_contact_submission():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "No data provided"}), 400

        submissions_collection.insert_one(data)

        # Custom subject line for General Contact submissions
        contact_subject = f"💬 GENERAL INQUIRY: {sanitize(data.get('name', 'Client'))} needs assistance"
        send_email_notification(data, subject_title=contact_subject)

        return jsonify({
            "success": True, 
            "message": "Message sent. Dalbagni Health will get back to you in the upcoming days."
        }), 200
    except Exception as e:
        print(f"Error handling contact submission: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

# In app.py
analytics_collection = db["analytics"]

@app.route('/api/track-view', methods=['POST'])
def track_view():
    try:
        # Increment total pageviews using MongoDB atomic operators
        analytics_collection.update_one(
            {"_id": "site_stats"},
            {"$inc": {"total_views": 1}},
            upsert=True
        )
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    
    # 2. Route to get dashboard statistics
@app.route('/api/admin/stats', methods=['GET'])
def get_dashboard_stats():
    try:
        # Fetch pageviews
        stats = analytics_collection.find_one({"_id": "site_stats"}) or {"total_views": 0}
        
        # Count conversions directly from MongoDB submissions
        contact_conversions = db["submissions"].count_documents({"formType": "Contact Inquiry"})
        quote_conversions = db["submissions"].count_documents({"formType": {"$ne": "Contact Inquiry"}})
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
        return jsonify({"success": False, "message": str(e)}), 500
    

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)