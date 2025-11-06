from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import AnyMessage, add_messages
from typing import Annotated, List
from yards.agents.discovery_agent import discovery_step
from yards.agents.rag_agent import RagAgent
from fastapi import WebSocket
from yards.utils.config import CONNECTED_CLIENTS

class DiscoveryState(dict):    
    user_id: str = ""
    file_path: str = ""
    filename: str = ""

def send_to_client(state):
    client_id = state["user_id"]


async def rag_node(state):
    try:
        rag_agent = RagAgent()
        
        query = state.get("user_input", "")
        doc_values = rag_agent.retrieve(query, k=3)
        state["doc_values"] = doc_values

        return state
    except Exception as e:
        print(e)


def execution_agent(state):
    return 


def redirect_node(state):
    if state['done'] and (state['user_input'] == 'yes' or state['user_input'] == 'ok'):
        return "execute"
    return "discovery"


workflow = StateGraph(DiscoveryState)
# workflow.add_node("rag", rag_node)

async def process_file(state):
    return await discovery_step(state)

workflow.add_node("discovery", process_file)

# workflow.add_edge("discovery", "rag")

workflow.set_entry_point("discovery")

memory = MemorySaver()
discovery_graph = workflow.compile(checkpointer=memory)