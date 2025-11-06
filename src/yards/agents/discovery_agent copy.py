import json
import asyncio
import os
from langchain_core.messages import HumanMessage, AIMessage
from yards.memory.conversation_memory import memory
from yards.memory.qdrant_memory import get_session_history, store_message
from yards.utils.config import KEY_CREDENTIALS
from yards.agents.validation_agent import validation_agent
import yards.utils.config as config
from yards.utils.utils import llm_init, call_llm

llm, prompt = llm_init()


# Async discovery step
async def discovery_step(state, websocket):    
    if state.get('done') and (state.get('user_input')).lower() in ['yes','ok']:
        await asyncio.sleep(2)
        await websocket.send_json({
                "status": 200,
                "agent": "🚀 Your migration is starting... This may take a few minutes. Please wait patiently ⏳",
        })
        try:
            await asyncio.sleep(2)
            await websocket.send_json({
                "status": 200,
                "ready_for_migration": True,
                "message": "⚡ The execution agent is currently running and processing your request.",
            })
            import_status = await init(websocket, state['collected_info'], state['mapping_task_detail'])
            
            if import_status:
                validation = await validation_agent(state, websocket, state['mapping_task_detail'])
                print("Validation status:", validation)                
                return validation
            else:
                await websocket.send_json({
                    "status": 500,
                    "message": "❌ Migration failed due to an error during initialization."
                })
                
        except Exception as e:
            print("err", e)

    # Build user input message
    doc_values = state.get("doc_values", [])
    context_str = "\n\n".join(doc_values) if doc_values else ""
    user_msg_text = f"Context:\n{context_str}\n\nQuestion: {state['user_input']}"
    user_input_msg = HumanMessage(content=user_msg_text)

    # Store user message
    store_message(state.get('user_id', ''), state.get('user_id', ''), 'user', user_msg_text)

    # Get conversation history
    history = get_session_history(state.get('user_id', ''), state.get('user_id', ''))

    # -------------------------
    # LLM 1 → JSON Extractor
    # -------------------------
    extractor_prompt = f"""
    You are a JSON extractor.

    Task:
    From the user input, extract values for these fields: {", ".join(state["collected_info"].keys())}

    Field meanings:
    {json.dumps(KEY_CREDENTIALS, indent=2)}

    Guidelines:
    - Consider BOTH natural sentences ("My IDMC username is") AND direct values ( "root", "Databricks").
    - If the user input is just a single value, map it to the MOST RECENTLY ASKED missing field.
    - Prioritize the latest user input over older history.
    - Include only fields explicitly provided (by sentence or value).
    - Do not add null, empty, or N/A values.
    - If user says "already provided" → skip it, keep the current value.
    - Always respond with ONLY a valid JSON object (no text, no explanation).
    - Only fill a field if the value clearly matches its definition.
    - Never confuse passwords with URLs or tokens.

    Conversation history: {history}
    Latest user input: "{state['user_input']}"
    """

    extracted = {}
    try:
        extractor_response = await call_llm(llm, prompt, """You are a JSON extractor. Only return valid JSON.""", extractor_prompt)
        raw_json = extractor_response.content.strip()
        extracted = json.loads(raw_json)
    except Exception as e:
        print("Extractor failed:", e)
        extracted = {}

    # Merge extracted values into collected_info
    for key, value in extracted.items():
        if value in ["already provided", "N/A", None, ""]:
            continue
        if key in state["collected_info"] and not state["collected_info"][key]:
            state["collected_info"][key] = value
    
    if state.get("initial_prompt") is True and state["collected_info"].get("source_api_url") is not None and state["collected_info"].get("source_api_token") is not None:
        print(f"Initial prompt block {websocket}")
        await websocket.send_json({
            "status": 200,
            "ready_for_migration": True,
            "message": "⏳ Just a moment… The discovery agent is analyzing your folders and mapping structure. 🗂️",
        })
        try:
            state["initial_prompt"] = False
            host = state["collected_info"].get("source_api_url", "")
            tok = state["collected_info"].get("source_api_token", "")
            if host and tok:
                config.DATABRICKS_HOST = host
                config.TOKEN = tok
                all_files = list_workspace("/")
                await websocket.send_json({
                    "status": 200,
                    "ready_for_migration": True,
                    "message": f"✅ Here’s the folder and mapping we found on your Databricks platform:",
                })
                # print("all_files", all_files)
                
                for file in all_files:
                    folder_path, mapping_name = os.path.split(file)
                    await websocket.send_json({
                        "status": 200,
                        "message": f"Folder: {folder_path}, Mapping name: {mapping_name}",
                        "ready_for_migration": True,                    
                    })
                
                await websocket.send_json({
                        "status": 200,
                        "client_id": state.get('user_id', ''),
                        "agent": f"I've discovered {len(all_files)} mappings in your Databricks workspace"                  
                    })
                
        except Exception as e:
            print("err", e)

    try:
        # -------------------------
        # Compute missing fields AFTER extraction
        # -------------------------
        missing_fields = [k for k, v in state["collected_info"].items() if not v]

        if missing_fields:
            next_field = missing_fields[0]
            # Prepare system prompt for conversational LLM
            SYSTEM_PROMPT_STRING_ABOVE = f"""
            You are Discovery AI Assistant for migrating mappings into Informatica IDMC.

            Missing field (do NOT show this to user): {KEY_CREDENTIALS[next_field]}

            Style & Behavior:
            - Be polite and brief (1 short sentence).
            - Ask ONLY for this missing detail: {next_field}.
            - Ask question from the missing fields in order until filled.
            - Never repeat or display credentials once the user provides them.
            - If the user cannot share a credential, store placeholder {{CREDENTIAL_NAME}}.
            - If all required values are filled, respond: "✅ All required values are collected."
            - Never echo user inputs or credentials.
            """
        else:
            SYSTEM_PROMPT_STRING_ABOVE = """
            You are Discovery AI Assistant for migrating mappings into Informatica IDMC.
            ✅ All required values are collected.

            Your task:
            - Politely confirm this.
            - Then ask: "We got the necessary information. Can we migrate mappings from Databricks to IDMC?"
            """

        # Conversational LLM call
        try:
            response = await call_llm(llm, prompt, SYSTEM_PROMPT_STRING_ABOVE, history + [user_input_msg])            
        except Exception as e:
            print(f"LLM 2 (Conversational) error: {e}")
            return state

        ai_msg = AIMessage(content=response.content)
        store_message(state.get('user_id', ''), state.get('user_id', ''), 'agent', response.content)

        # -------------------------
        # Final state update
        # -------------------------
        state["done"] = all(state["collected_info"].values())
        state["last_answer"] = response.content if not state["done"] else \
            "👋 Hey user, I’ve received all the required credentials. Would you like me to proceed with migrating the mappings shown on the right side?"

        # # Send update to frontend
        # if websocket:
        #     asyncio.create_task(websocket.send_json({
        #         "status": "intermediate",
        #         "agent": response.content,
        #         "collected_info": state["collected_info"]
        #     }))
        
        
        await websocket.send_json({
            "status": 200,
            "client_id": state.get('user_id', ''),
            "phase": "discovery",
            "agent": state["last_answer"],
            "collected_info": state["collected_info"]
        })

        return {
            "history": [user_input_msg, ai_msg],
            "last_answer": state["last_answer"],
            "collected_info": state["collected_info"],
            "done": state["done"]
        }

    except Exception as e:
        print(e)
