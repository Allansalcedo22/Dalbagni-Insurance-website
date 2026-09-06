import os
import certifi
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend requests

# Get environment variables
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

def send_email_notification(data):
    """Sends an email notification via Gmail SMTP when a new form is submitted."""
    if not email_user or not email_pass:
        print("Email credentials missing in .env — skipping email notification.")
        return

    try:
        subject = f"New Quote Request: {data.get('formType', 'Website Form')}"
        
        # Build readable email body
        body = f"""
New Form Submission Received!

Form Type: {data.get('formType', 'N/A')}
Name: {data.get('name', 'N/A')}
Email: {data.get('email', 'N/A')}
Phone: {data.get('phone', 'N/A')}
Message: {data.get('message', 'N/A')}
        """

        msg = MIMEMultipart()
        msg['From'] = email_user
        msg['To'] = notification_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        # Connect to Gmail SMTP Server
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(email_user, email_pass)
        server.send_message(msg)
        server.quit()

        print("Notification email sent successfully!")
    except Exception as e:
        print(f"Failed to send email: {e}")

@app.route('/api/submit-form', methods=['POST'])
def submit_form():
    try:
        data = request.get_json()

        if not data:
            return jsonify({"success": False, "message": "No data provided"}), 400

        # 1. Save to MongoDB
        result = submissions_collection.insert_one(data)
        print(f"Inserted document ID: {result.inserted_id}")

        # 2. Send Email Notification
        send_email_notification(data)

        return jsonify({"success": True, "message": "Form submitted successfully!"}), 200

    except Exception as e:
        print(f"Error handling form submission: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)