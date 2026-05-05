from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from yards.agents.compare_agent import compare_agent
from yards.agents.rag_agent import RagAgent

class CompareState(dict):    
    file1_name: str = ''
    file2_name: str = ''
    output_file_name: str = ''
    output_file_path: str = ''


workflow = StateGraph(CompareState)

async def compare(state):
    return await compare_agent(state)

workflow.add_node("compare", compare)

workflow.set_entry_point("compare")

memory = MemorySaver()
compare_graph = workflow.compile()