from SmartApi import SmartConnect
import pyotp
from dotenv import load_dotenv
import os

from fetch_token import get_token

load_dotenv(override=True)

API_KEY = os.getenv("ANGEL_API_KEY")
CLIENT_CODE = os.getenv("ANGEL_CLIENT_CODE")
PASSWORD = os.getenv("ANGEL_PASSWORD")
TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET")

smartApi = SmartConnect(api_key=API_KEY)

totp = pyotp.TOTP(TOTP_SECRET).now()

data = smartApi.generateSession(
    CLIENT_CODE,
    PASSWORD,
    totp
)

print(data)

feed_token = smartApi.getfeedToken()

print("Login Successful")



historicParam = {
    "exchange": "NSE",
    "symboltoken": get_token("RELIANCE-EQ"),
    "interval": "FIFTEEN_MINUTE",
    "fromdate": "2026-06-05 09:15",
    "todate": "2026-06-05 15:30"
}

data = smartApi.getCandleData(historicParam)

print(data)

