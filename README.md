# Dalbagni Insurance - Full-Stack Quote & Lead Management System

A full-stack web application designed for Dalbagni Insurance to collect, manage, and process customer quote requests in real time. Features a dynamic front-end form coupled with a modern Python/Flask REST API, cloud database storage, and automated email notifications.

---

## 🛠️ Tech Stack & Architecture

- **Frontend:** HTML5, CSS3, JavaScript (ES6+), Fetch API
- **Backend:** Python 3, Flask REST API, Flask-CORS
- **Database:** MongoDB Atlas (Cloud NoSQL Database via PyMongo)
- **Email Service:** Python `smtplib` / MIME (Gmail SMTP with App Passwords)
- **Environment Management:** `python-dotenv`, virtual environments (`venv`)

---

## ✨ Key Features

- **Automated Lead Capture:** Processes custom quote requests and contact forms via dynamic API endpoints.
- **Cloud Database Integration:** Stores form entries directly into MongoDB Atlas with SSL/TLS encryption.
- **Real-Time Email Alerts:** Automatically triggers email notifications to the agent upon every submission.
- **Secure Configuration:** Uses environment variables (`.env`) to safely manage database URIs, API keys, and email credentials.
- **CORS-Enabled API:** Built to handle cross-origin requests securely between local frontend interfaces and backend routes.

---

## 🚀 Getting Started

### Prerequisites
- Python 3.x
- MongoDB Atlas Account
- Node.js / Local HTTP Server (optional, for frontend local hosting)

### Installation & Setup

1. **Clone the Repository:**
   ```bash
   git clone [https://github.com/your-username/dalbagni-insurance.code](https://github.com/your-username/dalbagni-insurance.code)
   cd dalbagni-insurance/backendJuanse