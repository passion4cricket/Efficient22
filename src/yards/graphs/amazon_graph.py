import logging
from langgraph.graph import StateGraph
from langgraph.checkpoint.memory import MemorySaver
from yards.agents.amazon_agent import amazon_step
from yards.utils.config import CONNECTED_CLIENTS


class AmazonState(dict):
    user_id: str = ""
    file_path: str = ""
    filename: str = ""
    region: str = ""
    message: str = ""


workflow = StateGraph(AmazonState)

async def process_file(state):
    logging.info(f"[amazon_graph] process_file called user_id={state.get('user_id')} filename={state.get('filename')} file_path={state.get('file_path')}")
    result = await amazon_step(state)
    logging.info(f"[amazon_graph] process_file result status={result.get('status')} output_file={result.get('output_file_path')}")
    return result

workflow.add_node("amazon", process_file)
workflow.set_entry_point("amazon")

memory = MemorySaver()
amazon_graph = workflow.compile(checkpointer=memory)
