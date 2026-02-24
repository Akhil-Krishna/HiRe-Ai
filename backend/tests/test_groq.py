import asyncio
import httpx
import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()

async def test_groq():
    api_key = os.getenv("GROQ_API_KEY")
    print(f"API Key starts with: {api_key[:10]}...")
    
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": "Say 'Hello, API is working!'"}],
        "temperature": 0.7,
        "max_tokens": 1024,
    }
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            print(f"Status: {resp.status_code}")
            print(f"Response: {resp.text}")
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(test_groq())