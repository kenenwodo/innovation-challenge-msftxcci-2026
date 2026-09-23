import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
api_key = os.environ["AZURE_OPENAI_API_KEY"]
model = os.environ["AZURE_OPENAI_MODEL"]

client = OpenAI(
    base_url=endpoint,
    api_key=api_key,
)

response = client.responses.create(
    model=model,
    input="Reply with exactly: Python connection successful",
)

print(response.output_text)