from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from yards.agents.productname_compare_agent import product_name_compare
from yards.agents.rag_agent import RagAgent
from fastapi import WebSocket
from yards.utils.config import CONNECTED_CLIENTS

class ProductNameCompareState(dict):    
    user_id: str = ""
    file_path: str = ""
    filename: str = ""
    region: str = ""

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
    return "product_name_compare"


workflow = StateGraph(ProductNameCompareState)
# workflow.add_node("rag", rag_node)

async def process_file(state):
    return await product_name_compare(state)

workflow.add_node("product_name_compare", process_file)

# workflow.add_edge("product_name_compare", "rag")

workflow.set_entry_point("product_name_compare")

memory = MemorySaver()
productname_graph = workflow.compile(checkpointer=memory)