import os  # 2026-09-30: segredos vêm do ambiente
from twilio.rest import Client

account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")

print("SID:", account_sid)
print("Token len:", len(auth_token))
print("Token prefix:", auth_token[:4])

client = Client(account_sid, auth_token)

message = client.messages.create(
    from_="whatsapp:+14155238886",
    to="whatsapp:+5548984046118",
    body="Teste Twilio Sandbox funcionando"
)

print("Mensagem enviada:", message.sid)
