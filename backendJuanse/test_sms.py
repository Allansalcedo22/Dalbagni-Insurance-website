import smtplib

# 1. Fill in your credentials directly here to test
EMAIL_USER = "juandalbagnihealth@gmail.com"
EMAIL_PASS = "iawmltvomfgsyihd"  # Replace with the 16-character Google code

# 2. Replace @vtext.com with your actual carrier domain if you don't have Verizon
# Verizon = @vtext.com | AT&T = @txt.att.net | T-Mobile = @tmomail.net
MY_CARRIER_EMAIL = "7546108885@txt.att.net" 

try:
    print("Connecting to Gmail SMTP server...")
    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    
    print("Logging in...")
    server.login(EMAIL_USER, EMAIL_PASS)
    
    print("Sending test SMS...")
    server.sendmail(EMAIL_USER, MY_CARRIER_EMAIL, "Juan se sos un gordo forro Vamos Guerrero")
    server.quit()
    
    print("\nSUCCESS: Check your phone for a text message!")

except Exception as e:
    print(f"\nFAILED: {e}")