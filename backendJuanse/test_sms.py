"""Manual check that Gmail SMTP credentials in .env work, by sending a test text via a carrier email gateway."""
import os
import smtplib
from dotenv import load_dotenv

load_dotenv()

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")

# Carrier gateway address, e.g. 5551234567@txt.att.net
# Verizon = @vtext.com | AT&T = @txt.att.net | T-Mobile = @tmomail.net
MY_CARRIER_EMAIL = os.getenv("DESTINATION_CARRIER_SMS")

if not EMAIL_USER or not EMAIL_PASS or not MY_CARRIER_EMAIL:
    raise SystemExit("Set EMAIL_USER, EMAIL_PASS and DESTINATION_CARRIER_SMS in .env first.")

try:
    print("Connecting to Gmail SMTP server...")
    with smtplib.SMTP('smtp.gmail.com', 587) as server:
        server.starttls()

        print("Logging in...")
        server.login(EMAIL_USER, EMAIL_PASS)

        print("Sending test SMS...")
        server.sendmail(EMAIL_USER, MY_CARRIER_EMAIL, "Dalbagni test message: SMS notifications are working.")

    print("\nSUCCESS: Check your phone for a text message!")

except Exception as e:
    print(f"\nFAILED: {e}")
