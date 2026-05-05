import json
import uuid
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from pathlib import Path
import sys, os
import uvicorn
import logging
from datetime import datetime

if getattr(sys, "frozen", False):
    base_path = Path(sys._MEIPASS) / "yards"
    mf2py_data = Path(sys._MEIPASS) / "mf2py" / "backcompat-rules"
    os.environ["MF2PY_BACKCOMPAT_PATH"] = str(mf2py_data)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(
        Path(sys._MEIPASS) / "ms-playwright"
    )
else:
    base_path = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(base_path))

from yards.utils.config import set_env_path, CONNECTED_CLIENTS

if getattr(sys, "frozen", False):
    set_env_path(Path(sys._MEIPASS) / ".env")
else:
    set_env_path(".env")

from yards.graphs.discovery_graph import discovery_graph, DiscoveryState
from yards.graphs.compare_graph import compare_graph, CompareState
from yards.graphs.productname_compare_graph import productname_graph, ProductNameCompareState
from yards.graphs.amazon_graph import amazon_graph, AmazonState

# ── Shared helpers (re-use the same LLM stack as the agent app) ──────────────
from yards.utils.utils import llm_init, call_llm, parse_json_output

UPLOAD_DIR = os.path.join("uploads", "original_files")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ── App ───────────────────────────────────────────────────────────────────────
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


# ── Existing endpoints ────────────────────────────────────────────────────────

@app.post("/upload")
async def generate_shopify_format(file: UploadFile = File(...)):
    client_id = str(uuid.uuid4())
    CONNECTED_CLIENTS[client_id] = {"state": DiscoveryState()}

    try:
        filename = file.filename
        file_path = os.path.join(UPLOAD_DIR, filename)
        print(f"Received file: {filename}, saving to: {file_path}")

        if os.path.exists(file_path):
            name, ext = os.path.splitext(filename)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{name}_{timestamp}{ext}"
            file_path = os.path.join(UPLOAD_DIR, filename)

        with open(file_path, "wb") as f:
            f.write(await file.read())

        state = CONNECTED_CLIENTS[client_id]["state"]
        state["user_id"] = client_id
        state["file_path"] = file_path
        state["filename"] = filename
        state["region"] = ""

        config = {"configurable": {"thread_id": client_id}}
        state = await discovery_graph.ainvoke(state, config=config)
        CONNECTED_CLIENTS[client_id]["state"] = state
        return state

    except Exception as e:
        print(f"Error with client {client_id}: {e}")
        return {"status": 500, "message": str(e)}


@app.post("/upload_amazon")
async def generate_amazon_format(file: UploadFile = File(...)):
    client_id = str(uuid.uuid4())
    CONNECTED_CLIENTS[client_id] = {"state": AmazonState()}

    logging.info(f"[main] /upload_amazon entered client_id={client_id}")
    try:
        filename = file.filename
        file_path = os.path.join(UPLOAD_DIR, filename)
        logging.info(f"[main] Received file: {filename}, saving to: {file_path}")

        if os.path.exists(file_path):
            name, ext = os.path.splitext(filename)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{name}_{timestamp}{ext}"
            file_path = os.path.join(UPLOAD_DIR, filename)

        with open(file_path, "wb") as f:
            f.write(await file.read())

        state = CONNECTED_CLIENTS[client_id]["state"]
        state["user_id"] = client_id
        state["file_path"] = file_path
        state["filename"] = filename
        state["region"] = ""

        config = {"configurable": {"thread_id": client_id}}
        state = await amazon_graph.ainvoke(state, config=config)
        CONNECTED_CLIENTS[client_id]["state"] = state
        logging.info(f"[main] /upload_amazon completed client_id={client_id} output_keys={list(state.keys())}")
        return state

    except Exception as e:
        logging.error(f"[main] /upload_amazon failed client_id={client_id} error={e}", exc_info=True)
        return {"status": 500, "message": str(e)}


@app.post("/compare")
async def compare_fields(
    file1: UploadFile = File(...), file2: UploadFile = File(...)
):
    print(file1, file2)
    save_dir = "uploads/compare_files"
    os.makedirs(save_dir, exist_ok=True)

    file1_path = os.path.join(save_dir, file1.filename)
    file2_path = os.path.join(save_dir, file2.filename)

    with open(file1_path, "wb") as f:
        f.write(await file1.read())
    with open(file2_path, "wb") as f:
        f.write(await file2.read())

    state = CompareState()
    state["file1_name"] = file1.filename
    state["file2_name"] = file2.filename
    state = await compare_graph.ainvoke(state)
    print(state)

    return FileResponse(
        path=state["output_file_path"],
        filename=state["output_file_name"],
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Access-Control-Expose-Headers": "Content-Disposition"},
    )


# ── Internal action helpers (SSE generators) ─────────────────────────────────

async def _run_upload(data: dict):
    file_path = data.get("file_path")
    filename = data.get("filename") or Path(file_path).name if file_path else None

    if not file_path or not Path(file_path).is_file():
        yield f'data: {json.dumps({"type": "message", "content": "File not found. Please upload a valid file."})}\n\n'
        return

    client_id = str(uuid.uuid4())
    CONNECTED_CLIENTS[client_id] = {"state": DiscoveryState()}

    try:
        state = CONNECTED_CLIENTS[client_id]["state"]
        state["user_id"] = client_id
        state["file_path"] = file_path
        state["filename"] = filename
        state["region"] = ""

        config = {"configurable": {"thread_id": client_id}}
        yield f'data: {json.dumps({"type": "message", "content": f"Processing {filename}..."})}\n\n'

        state = await discovery_graph.ainvoke(state, config=config)
        CONNECTED_CLIENTS[client_id]["state"] = state

        yield f'data: {json.dumps({"type": "message", "content": f"Shopify format generated for {filename}."})}\n\n'

    except Exception as e:
        logging.error(f"_run_upload error: {e}", exc_info=True)
        yield f'data: {json.dumps({"type": "error", "content": str(e)})}\n\n'

    finally:
        CONNECTED_CLIENTS.pop(client_id, None)


async def _run_upload_amazon(data: dict):
    file_path = data.get("file_path")
    filename = data.get("filename") or Path(file_path).name if file_path else None

    if not file_path or not Path(file_path).is_file():
        yield f'data: {json.dumps({"type": "message", "content": "File not found. Please upload a valid file."})}\n\n'
        return

    client_id = str(uuid.uuid4())
    CONNECTED_CLIENTS[client_id] = {"state": AmazonState()}

    try:
        state = CONNECTED_CLIENTS[client_id]["state"]
        state["user_id"] = client_id
        state["file_path"] = file_path
        state["filename"] = filename
        state["region"] = ""

        config = {"configurable": {"thread_id": client_id}}
        yield f'data: {json.dumps({"type": "message", "content": f"Processing {filename}..."})}\n\n'

        state = await amazon_graph.ainvoke(state, config=config)
        CONNECTED_CLIENTS[client_id]["state"] = state

        yield f'data: {json.dumps({"type": "message", "content": f"Amazon format generated for {filename}."})}\n\n'

    except Exception as e:
        logging.error(f"_run_upload_amazon error: {e}", exc_info=True)
        yield f'data: {json.dumps({"type": "error", "content": str(e)})}\n\n'

    finally:
        CONNECTED_CLIENTS.pop(client_id, None)


async def _run_compare(data: dict):

    file1_path = data.get("file1_path")
    file2_path = data.get("file2_path")

    for label, path in [("file1", file1_path), ("file2", file2_path)]:
        if not path or not Path(path).is_file():
            yield f'data: {json.dumps({"type": "message", "content": f"{label} not found. Please provide a valid file path."})}\n\n'
            return

    try:
        yield f'data: {json.dumps({"type": "message", "content": "Comparing files..."})}\n\n'

        state = CompareState()
        state["file1_name"] = Path(file1_path).name
        state["file2_name"] = Path(file2_path).name
        state = await compare_graph.ainvoke(state)

        message = f"Comparison complete. Output: {state.get('output_file_name', 'result.xlsx')}"
        yield "data: " + json.dumps({"type": "message", "content": message}) + "\n\n"
        yield "data: " + json.dumps({"type": "download", "content": state.get("output_file_path", "")}) + "\n\n"

    except Exception as e:
        logging.error(f"_run_compare error: {e}", exc_info=True)
        yield f'data: {json.dumps({"type": "error", "content": str(e)})}\n\n'

async def _run_productname_compare(e22_access_token: str):

    client_id = str(uuid.uuid4())
    CONNECTED_CLIENTS[client_id] = {"state": ProductNameCompareState()}

    try:
        state = CONNECTED_CLIENTS[client_id]["state"]
        state["user_id"] = client_id
        state["e22_access_token"] = e22_access_token

        config = {"configurable": {"thread_id": client_id}}

        state = await productname_graph.ainvoke(state, config=config)
        CONNECTED_CLIENTS[client_id]["state"] = state

    except Exception as e:
        logging.error(f"_run_productname_compare error: {e}", exc_info=True)
        yield f'data: {json.dumps({"type": "error", "content": str(e)})}\n\n'

    finally:
        CONNECTED_CLIENTS.pop(client_id, None)


# ── Router endpoint ───────────────────────────────────────────────────────────

TECHNOLOGY_TO_ENDPOINT = {
    "generate shopify format": "upload",
    "compare": "compare",
    "product name compare": "Productname_compare",
    "generate amazon format": "amazon",
    "amazon": "amazon",
}


def endpoint_from_technology(technology: str | None) -> str | None:
    if not technology:
        return None
    return TECHNOLOGY_TO_ENDPOINT.get(technology.strip().lower())


class RouterRequest(BaseModel):
    application: str
    user_query: str
    unique_session_id: str
    technology: str | None = None
    e22_access_token: str | None = None
    # Optional pre-resolved file paths (populated by the frontend after upload)
    file_path: str | None = None
    file1_path: str | None = None
    file2_path: str | None = None


@app.post("/router")
async def action_router(request: RouterRequest):
    async def sse_generator():
        from yards.memory.qdrant_memory import store_message, get_session_history

        user_id = "default_user"
        session_id = request.unique_session_id or "default_session"
        application = request.application
        user_query = request.user_query
        technology = request.technology or None
        e22_access_token = request.e22_access_token or None
        mapped_endpoint = endpoint_from_technology(technology)

        print(f"Router received query: {user_query} for application: {application} with session_id: {session_id} and technology: {technology}")
        if mapped_endpoint:
            print(f"Technology '{technology}' maps to endpoint '{mapped_endpoint}'")

        # 1. Persist incoming message
        store_message(
            user_id=user_id,
            session_id=session_id,
            role="user",
            message=user_query,
            application=application,
        )

        # 2. Retrieve recent context for the LLM
        recent_history_list = get_session_history(
            user_id=user_id,
            session_id=session_id,
            application=application,
            limit=5,
        )
        conversation_context = "\n".join(recent_history_list)

        yield f'data: {json.dumps({"type": "message", "content": "Analysing request..."})}\n\n'

        llm, prompt = llm_init()

        # ── System prompt ──────────────────────────────────────────────────
        system_prompt = f"""You are an intelligent API router for a Shopify file-conversion tool.
Based on the user's query, decide which internal action should be taken.

Available endpoints and when to use them:
1. "upload"   – Convert / process a single product file into Shopify format.
               Requires: "filename" (e.g. "products.csv").
2. "amazon"   – Convert / process a single product file into Amazon format.
               Requires: "filename" (e.g. "products.csv").
3. "compare"  – Compare two files and highlight differences.
               Requires: "file1_name" and "file2_name".
4. "Productname_compare"  - Compare product names in a file and generate a report.
5. "chatbot"  – General questions, help, or anything that does NOT involve
               file processing.

Rules:
- If the query asks to process, convert, or generate Shopify format for ONE file, choose "upload".
- If the query asks to process, convert, or generate Amazon format for ONE file, choose "amazon".
- If the query asks to compare, diff, or contrast TWO files, choose "compare".
- For all other queries choose "chatbot".
- If a required filename is missing, set "missing_requirements" to a friendly
  question asking the user for it; otherwise set it to null.

Respond with ONLY a JSON object in this EXACT format:
{{
    "endpoint": "upload" | "amazon" | "compare" | "Productname_compare" | "chatbot",
    "filename": "file.csv or null",
    "file1_name": "file1.csv or null",
    "file2_name": "file2.csv or null",
    "missing_requirements": "Friendly question if any required info is missing, else null"
}}
"""
        user_prompt = (
            f"Application: {application}\n"
            f"Query: {user_query}\n"
            f"Conversation history:\n{conversation_context}\n"
            f"Technology: {technology if technology else 'not specified'}"
        )

        try:
            response = await call_llm(llm, prompt, system_prompt, user_prompt)
            decision = parse_json_output(response.content)

            endpoint = decision.get("endpoint")
            filename = decision.get("filename")
            file1_name = decision.get("file1_name")
            file2_name = decision.get("file2_name")
            missing_reqs = decision.get("missing_requirements")

            if mapped_endpoint:
                endpoint = mapped_endpoint
                logging.info(f"[yards/router] overriding LLM endpoint with technology-based endpoint: {endpoint}")

            logging.info(
                f"[yards/router] endpoint={endpoint}, missing={missing_reqs}"
            )

            # ── Missing info → ask the user ────────────────────────────────
            if missing_reqs:
                store_message(
                    user_id=user_id,
                    session_id=session_id,
                    role="assistant",
                    message=missing_reqs,
                    application=application,
                )
                yield f'data: {json.dumps({"type": "message", "content": missing_reqs})}\n\n'
                yield f'data: {json.dumps({"type": "done", "content": ""})}\n\n'
                return

            # ── Dispatch ───────────────────────────────────────────────────
            if endpoint == "upload":
                # Prefer explicitly provided path; fall back to UPLOAD_DIR lookup
                file_path = request.file_path or os.path.join(UPLOAD_DIR, filename)
                async for chunk in _run_upload({"file_path": file_path, "filename": filename}):
                    yield chunk

            elif endpoint == "amazon":
                file_path = request.file_path or os.path.join(UPLOAD_DIR, filename)
                async for chunk in _run_upload_amazon({"file_path": file_path, "filename": filename}):
                    yield chunk

            elif endpoint == "compare":
                save_dir = "uploads/compare_files"
                file1_path = request.file1_path or os.path.join(save_dir, file1_name)
                file2_path = request.file2_path or os.path.join(save_dir, file2_name)
                async for chunk in _run_compare({"file1_path": file1_path, "file2_path": file2_path}):
                    yield chunk
            
            elif endpoint == "Productname_compare":
                async for chunk in _run_productname_compare(e22_access_token):
                    yield chunk

            elif endpoint == "chatbot":
                yield f'data: {json.dumps({"type": "error", "content": "Chatbot endpoint is not available in this deployment."})}\n\n'

            else:
                yield f'data: {json.dumps({"type": "error", "content": f"Unknown endpoint: {endpoint}"})}\n\n'

        except Exception as e:
            logging.error(f"[yards/router] error: {e}", exc_info=True)
            yield f'data: {json.dumps({"type": "error", "content": str(e)})}\n\n'

        yield f'data: {json.dumps({"type": "done", "content": ""})}\n\n'

    return StreamingResponse(sse_generator(), media_type="text/event-stream")


# ── Helpers ───────────────────────────────────────────────────────────────────

async def send_to_client(client_id: str, message: dict):
    client = CONNECTED_CLIENTS.get(client_id)
    # extend as needed


# ── Entry-point ───────────────────────────────────────────────────────────────

def main():
    uvicorn.run(app, host="127.0.0.1", port=5000)


if __name__ == "__main__":
    main()