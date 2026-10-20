"""Email delivery is replaceable; no codes in HTTP responses or ordinary logs."""
import os
import smtplib
import ssl
import json
import secrets
from pathlib import Path
from email.message import EmailMessage


def configured():
    backend=os.getenv('AUTH_EMAIL_BACKEND','smtp')
    if backend=='development':
        return os.getenv('APP_ENV')=='development' and bool(os.getenv('AUTH_DEV_MAILBOX_DIR'))
    return backend=='smtp' and bool(os.getenv('SMTP_HOST')) and bool(os.getenv('SMTP_FROM'))


def send_code(recipient, code, purpose):
    if not configured():
        raise ValueError('Email delivery is not configured')
    if os.getenv('AUTH_EMAIL_BACKEND','smtp')=='development':
        directory=Path(os.environ['AUTH_DEV_MAILBOX_DIR']).resolve()
        # Operators configure an explicit private directory; never a public asset.
        public=Path(__file__).resolve().parents[2]/'frontend'
        if directory==public or public in directory.parents:
            raise ValueError('Development mail must not be publicly served')
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        path=directory/(secrets.token_hex(16)+'.json')
        descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'w',encoding='utf8') as handle:
            json.dump({'to':recipient,'purpose':purpose,'code':code},handle)
        return
    message=EmailMessage()
    message['From']=os.environ['SMTP_FROM'];message['To']=recipient
    message['Subject']='Your DataRise verification code'
    action = 'set or reset your password' if purpose == 'RESET_PASSWORD' else 'verify your email'
    message.set_content(f'DataRise AI\n\nUse this one-time code to {action}: {code}\n\nIt expires in 10 minutes. If you did not request this, you can ignore this email.')
    host=os.environ['SMTP_HOST'];port=int(os.getenv('SMTP_PORT','587'))
    use_ssl=os.getenv('SMTP_SSL','false').lower()=='true'
    client=smtplib.SMTP_SSL(host,port,timeout=10,context=ssl.create_default_context()) if use_ssl else smtplib.SMTP(host,port,timeout=10)
    with client:
        if not use_ssl:client.starttls(context=ssl.create_default_context())
        if os.getenv('SMTP_USERNAME'):client.login(os.environ['SMTP_USERNAME'],os.environ['SMTP_PASSWORD'])
        client.send_message(message)
