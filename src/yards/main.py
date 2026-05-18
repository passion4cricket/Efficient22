import asyncio
import json
import uuid
from fastapi import FastAPI, File, UploadFile, Request
from fastapi.responses import FileResponse, StreamingResponse
from pathlib import Path
import sys, os
import uvicorn
import logging

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

CLIENTS_LOCK = asyncio.Lock()

async def register_client(state_cls):
    client_id = str(uuid.uuid4())
    state = state_cls()
    async with CLIENTS_LOCK:
        CONNECTED_CLIENTS[client_id] = {"state": state}
    return client_id, state

async def get_client_state(client_id):
    async with CLIENTS_LOCK:
        client = CONNECTED_CLIENTS.get(client_id)
    return client["state"] if client else None

async def update_client_state(client_id, state):
    async with CLIENTS_LOCK:
        if client_id in CONNECTED_CLIENTS:
            CONNECTED_CLIENTS[client_id]["state"] = state

async def unregister_client(client_id):
    async with CLIENTS_LOCK:
        CONNECTED_CLIENTS.pop(client_id, None)

if getattr(sys, "frozen", False):
    set_env_path(Path(sys._MEIPASS) / ".env")
else:
    set_env_path(".env")

from yards.graphs.discovery_graph import discovery_graph, DiscoveryState
from yards.graphs.compare_graph import compare_graph, CompareState
from yards.graphs.productname_compare_graph import productname_graph, ProductNameCompareState
from yards.graphs.amazon_graph import amazon_graph, AmazonState

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


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/shopify")
async def generate_shopify_format(file: UploadFile = File(...), request: Request = None):
    origin = f"{request.client.host}:{request.client.port}" if request and request.client else "unknown"
    client_id, state = await register_client(DiscoveryState)

    try:
        filename = file.filename
        unique_filename = f"{client_id}_{filename}"
        file_path = os.path.join(UPLOAD_DIR, unique_filename)
        logging.info(f"[upload] Received file: {filename} from {origin}, saving to: {file_path}")

        with open(file_path, "wb") as f:
            f.write(await file.read())

        state["user_id"] = client_id
        state["file_path"] = file_path
        state["filename"] = filename
        state["region"] = ""

        config = {"configurable": {"thread_id": client_id}}
        state = await discovery_graph.ainvoke(state, config=config)
        await update_client_state(client_id, state)
        return state

    except Exception as e:
        logging.error(f"[upload] Error with client {client_id}: {e}", exc_info=True)
        return {"status": 500, "message": str(e)}

    finally:
        await unregister_client(client_id)


@app.post("/amazon")
async def generate_amazon_format(file: UploadFile = File(...), request: Request = None):
    origin = f"{request.client.host}:{request.client.port}" if request and request.client else "unknown"
    client_id, state = await register_client(AmazonState)

    logging.info(f"[upload_amazon] Entered client_id={client_id} origin={origin}")
    try:
        filename = file.filename
        unique_filename = f"{client_id}_{filename}"
        file_path = os.path.join(UPLOAD_DIR, unique_filename)
        logging.info(f"[upload_amazon] Received file: {filename} from {origin}, saving to: {file_path}")

        with open(file_path, "wb") as f:
            f.write(await file.read())

        state["user_id"] = client_id
        state["file_path"] = file_path
        state["filename"] = filename
        state["region"] = ""

        config = {"configurable": {"thread_id": client_id}}
        state = await amazon_graph.ainvoke(state, config=config)
        await update_client_state(client_id, state)
        logging.info(f"[upload_amazon] Completed client_id={client_id} output_keys={list(state.keys())}")
        return state

    except Exception as e:
        logging.error(f"[upload_amazon] Failed client_id={client_id} error={e}", exc_info=True)
        return {"status": 500, "message": str(e)}

    finally:
        await unregister_client(client_id)


@app.post("/comparison")
async def compare_fields(
    request: Request,
    file1: UploadFile = File(...),
    file2: UploadFile = File(...),
):
    origin = f"{request.client.host}:{request.client.port}" if request and request.client else "unknown"
    save_dir = "uploads/compare_files"
    os.makedirs(save_dir, exist_ok=True)

    client_id = str(uuid.uuid4())
    file1_path = os.path.join(save_dir, f"{client_id}_{file1.filename}")
    file2_path = os.path.join(save_dir, f"{client_id}_{file2.filename}")
    logging.info(f"[compare] Request from {origin} file1={file1.filename} file2={file2.filename}")

    with open(file1_path, "wb") as f:
        f.write(await file1.read())
    with open(file2_path, "wb") as f:
        f.write(await file2.read())

    try:
        state = CompareState()
        state["file1_name"] = file1.filename
        state["file2_name"] = file2.filename
        state = await compare_graph.ainvoke(state)

        return FileResponse(
            path=state["output_file_path"],
            filename=state["output_file_name"],
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Access-Control-Expose-Headers": "Content-Disposition"},
        )

    except Exception as e:
        logging.error(f"[compare] Error: {e}", exc_info=True)
        return {"status": 500, "message": str(e)}


@app.post("/product_reconciliation")
async def productname_compare(request: Request):
    body = await request.json()
    e22_access_token = body.get("e22_access_token")
    origin = f"{request.client.host}:{request.client.port}" if request and request.client else "unknown"

    client_id, state = await register_client(ProductNameCompareState)
    logging.info(f"[productname_compare] Entered client_id={client_id} origin={origin}")

    try:
        state["user_id"] = client_id
        state["e22_access_token"] = e22_access_token

        config = {"configurable": {"thread_id": client_id}}
        state = await productname_graph.ainvoke(state, config=config)
        await update_client_state(client_id, state)
        logging.info(f"[productname_compare] Completed client_id={client_id}")
        return state

    except Exception as e:
        logging.error(f"[productname_compare] Error client_id={client_id}: {e}", exc_info=True)
        return {"status": 500, "message": str(e)}

    finally:
        await unregister_client(client_id)


# ── Entry-point ───────────────────────────────────────────────────────────────

def main():
    uvicorn.run(app, host="127.0.0.1", port=5001)


if __name__ == "__main__":
    main()