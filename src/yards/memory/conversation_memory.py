import json, os
from yards.utils.utils import get_base_dir

class ConversationMemory:
    def __init__(self, file_path="/discovery_memory.json"):
        file_path = rf"{get_base_dir()}/memory{file_path}"
        self.file_path = file_path
        if not os.path.exists(file_path):
            with open(file_path, "w") as f:
                json.dump([], f)

    def save_message(self, user, agent):
        with open(self.file_path, "r") as f:
            data = json.load(f)
        data.append({"user": user, "agent": agent})
        with open(self.file_path, "w") as f:
            json.dump(data, f, indent=2)

    def get_all(self):
        with open(self.file_path, "r") as f:
            return json.load(f)

memory = ConversationMemory()
