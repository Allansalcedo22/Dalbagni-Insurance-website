import os
import html
import certifi
import smtplib
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

def send_email_notification(data):
    """Loads email_template.html and transmits it via Gmail SMTP."""
    if not email_user or not email_pass or not notification_email:
        print("Email credentials or notification recipient missing in .env.")
        return

    try:
        # Load the HTML template file from disk
        template_path = os.path.join(os.path.dirname(__file__), 'email_template.html')
        with open(template_path, 'r', encoding='utf-8') as file:
            html_template = file.read()

        # Fill in the HTML template placeholders with sanitized client data
        rendered_html = html_template.format(
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
        msg['Subject'] = f"⚡ New Quote Request: {sanitize(data.get('name', 'Client'))}"

        # Attach rendered HTML body
        msg.attach(MIMEText(rendered_html, 'html'))

        # Send via Gmail SMTP
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(email_user, email_pass)
            server.send_message(msg)

        print("Formatted HTML email notification sent successfully!")
    except Exception as e:
        print(f"Failed to send email notification: {e}")

@app.route('/api/submit-form', methods=['POST'])
def submit_form():
    try:
        data = request.get_json()

        if not data:
            return jsonify({"success": False, "message": "No data provided"}), 400

        # 1. Save to MongoDB
        result = submissions_collection.insert_one(data)
        print(f"Inserted document ID: {result.inserted_id}")

        # 2. Send Styled Email
        send_email_notification(data)

        return jsonify({"success": True, "message": "Form submitted successfully!"}), 200

    except Exception as e:
        print(f"Error handling form submission: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)