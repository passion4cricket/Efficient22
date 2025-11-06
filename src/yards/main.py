import json
import uuid
from fastapi import FastAPI, File, UploadFile
from pathlib import Path
import sys, os
import uvicorn
from datetime import datetime
# from yards.main import app

if getattr(sys, "frozen", False):
    base_path = Path(sys._MEIPASS) / "yards"
else:
    base_path = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(base_path))

from yards.graphs.discovery_graph import discovery_graph, DiscoveryState
from yards.utils.config import CONNECTED_CLIENTS

UPLOAD_DIR = os.path.join("uploads", "original_files")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI()

@app.post("/upload")
async def discovery_endpoint(file: UploadFile = File(...)):
    client_id = str(uuid.uuid4())
    CONNECTED_CLIENTS[client_id] = {
        "state": DiscoveryState()
    }    

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
        state['user_id'] = client_id
        state['file_path'] = file_path
        state['filename'] = filename

        config={"configurable":{"thread_id":client_id}}
        
        state = await discovery_graph.ainvoke(state, config=config)
        CONNECTED_CLIENTS[client_id]["state"] = state
    except Exception as e:
        print(f"Error with client {client_id}: {e}")

# 🔹 Example: Send a message to a specific client from outside
async def send_to_client(client_id: str, message: dict):
    client = CONNECTED_CLIENTS.get(client_id)
        
def main():    
    uvicorn.run(app, host="127.0.0.1", port=8000)

if __name__ == "__main__":    
    main()
