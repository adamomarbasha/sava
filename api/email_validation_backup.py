import re
import socket
import smtplib
from email_validator import validate_email, EmailNotValidError
from typing import Tuple

def validate_email_comprehensive(email: str) -> Tuple[bool, str]:
    DISPOSABLE_DOMAINS = {
        '10minutemail.com', 'tempmail.org', 'guerrillamail.com',
        'mailinator.com', 'yopmail.com', 'temp-mail.org',
        'throwaway.email', 'getnada.com', 'maildrop.cc',
        'fakeinbox.com', 'trashmail.com', 'dispostable.com'
    }
    
    try:
        validated_email = validate_email(email)
        normalized_email = validated_email.email.lower()
        domain = validated_email.domain.lower()
        
        if domain in DISPOSABLE_DOMAINS:
            return False, "Temporary or disposable email addresses are not allowed"
        
        try:
            mx_records = socket.getaddrinfo(domain, None)
            if not mx_records:
                return False, "Email domain does not exist"
        except socket.gaierror:
            return False, "Email domain does not exist"
        
        local_part = validated_email.email.split('@')[0]
        
        if re.match(r'^[a-zA-Z]+\d{8,}$', local_part):
            return False, "Please use a valid email address"
        
        if len(local_part) > 20 and re.search(r'\d{3,}', local_part):
            return False, "Please use a valid email address"
        
        if len(local_part) > 15 and re.search(r'[a-zA-Z]+\d+[a-zA-Z]+\d+', local_part):
            return False, "Please use a valid email address"
            
        return True, ""
        
    except EmailNotValidError as e:
        return False, f"Invalid email format: {str(e)}"
    except Exception as e:
        print(f"Email validation error for {email}: {e}")
        return True, ""


def is_email_format_valid(email: str) -> bool:
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None 