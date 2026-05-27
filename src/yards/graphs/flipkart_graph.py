import logging
from langgraph.graph import StateGraph
from langgraph.checkpoint.memory import MemorySaver
from yards.agents.flipkart_agent import flipkart_step
from yards.utils.config import CONNECTED_CLIENTS


class FlipkartState(dict):
    user_id: str = ""
    file_path: str = ""
    filename: str = ""
    region: str = ""
    message: str = ""


workflow = StateGraph(FlipkartState)

async def process_file(state):
    logging.info(f"[flipkart_graph] process_file called user_id={state.get('user_id')} filename={state.get('filename')} file_path={state.get('file_path')}")
    result = await flipkart_step(state)
    logging.info(f"[flipkart_graph] process_file result status={result.get('status')} output_file={result.get('output_file_path')}")
    return result

workflow.add_node("flipkart", process_file)
workflow.set_entry_point("flipkart")

memory = MemorySaver()
flipkart_graph = workflow.compile(checkpointer=memory)
