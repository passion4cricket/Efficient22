from pathlib import Path
import os, sys, json, re
from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq
from yards.utils.config import PROFIT_MARGIN
from dotenv import load_dotenv
import ollama
import yfinance as yf
import asyncio

_model = None

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

def get_base_dir():
    if getattr(sys, 'frozen', False):
        # Running from PyInstaller EXE
        base_path = Path(sys._MEIPASS) / "yards"
        return base_path
    else:
        # Running in normal Python environment               
        base_path = Path(__file__).resolve().parent.parent
        return base_path
    
def llm_init():
    # Initialize Groq LLM
    llm = ChatGroq(
        groq_api_key=GROQ_API_KEY,
        model_name="llama-3.1-8b-instant",
        temperature=0,
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", "{system_prompt}"),
        ("human", "{user_input}")
    ])

    return llm, prompt


async def call_llm(llm, prompt, system_prompt, user_input):
    response = await llm.ainvoke(prompt.format_messages(
        system_prompt=system_prompt,
        user_input=user_input
    ))

    return response


def parse_json_output(text):
    # Remove anything before the first { or [
    match = re.search(r'[\{\[]', text)
    if not match:
        raise ValueError("No JSON object/array found")
    text = text[match.start():].strip()
    
    # Optional: remove any trailing characters after the last } or ]
    last_brace = max(text.rfind('}'), text.rfind(']'))
    if last_brace != -1:
        text = text[:last_brace+1]

    return json.loads(text)

async def run_local_llm(system_prompt, user_prompt):
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: ollama.chat(
            model="phi-fast",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            options={
                "gpu_layers": 20,
                "num_predict": 4096,   # ⚡ FIXED
                "temperature": 0.1,    # Stable JSON
                "top_p": 1,
                "repeat_penalty": 1.0
            }
        )
    )
    
    return response["message"]["content"]


def price_conversion(cost_price, lowest_price_websites):    
    # cost_price + (cost_price * PROFIT_MARGIN / 100)
    calculated_price = cost_price * (1 + PROFIT_MARGIN / 100)
    
    if calculated_price < lowest_price_websites:
        calculated_price = (calculated_price+lowest_price_websites) / 2
    
    return calculated_price

def convert_inr_to_usd(amount_in_inr):
    rate = yf.Ticker("INR=X").fast_info["last_price"]
    print(f"INR to USD rate: {rate}, Amount in INR: {amount_in_inr}")
    amount_in_usd = amount_in_inr / rate
    print(f"Converted Amount in USD: {amount_in_usd}")
    return round(amount_in_usd, 2)