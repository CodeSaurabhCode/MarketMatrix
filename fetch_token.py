import pandas as pd
import requests
import json

FILE_NAME = "OpenAPIScripMaster.json"

def save_tokens():
    url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

    response = requests.get(url)
    response.raise_for_status()

    with open(FILE_NAME, "w", encoding="utf-8") as f:
        json.dump(response.json(), f)

    print("Saved successfully")

def get_token(symbol):
    df = pd.read_json(FILE_NAME)

    result = df[df["symbol"] == symbol]

    if result.empty:
        return None

    return result.iloc[0]["token"]

if __name__ == "__main__":
    save_tokens()

    token = get_token("RELIANCE-EQ")
    print(token)