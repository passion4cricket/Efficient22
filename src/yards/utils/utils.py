from pathlib import Path
import os, sys, json, re
from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq
# from yards.utils.config import GROQ_API_KEY
from dotenv import load_dotenv

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