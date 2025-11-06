from qdrant_client import QdrantClient
from qdrant_client.models import (
    Filter,
    FieldCondition,
    MatchValue,
    VectorParams,
    Distance,
    PointStruct,
    PayloadSchemaType
)
from sentence_transformers import SentenceTransformer
import uuid, re, os
import time
# from yards.utils.config import QDRANT_HOST, QDRANT_API_KEY, GROQ_API_KEY
from groq import Groq
from dotenv import load_dotenv

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
QDRANT_HOST = os.getenv("QDRANT_HOST")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")

# ----------------------------
# Paraphrase dictionary
# ----------------------------
fields_with_phrases = {
    "source_platform": [
        "you are migrating from (source platform)",
        "the source system is (source platform)",
        "data is being moved from (source platform)",
        "extracting from (source platform)",
        "migration starts from (source platform)"
    ],
    "mapping_type": [
        "the type of mapping being migrated",
        "which mapping are we migrating",
        "migration mapping type",
        "mapping classification"
    ],
    "host_url": [
        "the host URL for your Source workspace",
        "Source workspace address",
        "Source server URL",
        "Source cluster host"
    ],
    "Source_token": [
        "the authentication token for Source",
        "Source personal access token",
        "Source API key",
        "token to connect to Source"
    ],
    "idmc_username": [
        "your Informatica IDMC username",
        "IDMC login user",
        "Informatica Cloud username",
        "IDMC account user"
    ],
    "idmc_password": [
        "your Informatica IDMC password",
        "IDMC login password",
        "Informatica Cloud password",
        "IDMC account password"
    ],
    "source_db_host": [
        "the host address of your source database",
        "source DB host",
        "source database server",
        "origin database hostname"
    ],
    "source_db_username": [
        "the username for your source database",
        "source DB user",
        "origin database login name",
        "credentials for source DB user"
    ],
    "source_db_password": [
        "the password for your source database",
        "source DB passkey",
        "origin database login password",
        "credentials for source DB password"
    ],
    "source_db_name": [
        "the name of your source database",
        "source DB schema name",
        "origin database name",
        "database you are extracting from"
    ],
    "target_db_host": [
        "the host address of your target database",
        "target DB host",
        "target database server",
        "destination database hostname"
    ],
    "target_db_username": [
        "the username for your target database",
        "target DB user",
        "destination database login name",
        "credentials for target DB user"
    ],
    "target_db_password": [
        "the password for your target database",
        "target DB passkey",
        "destination database login password",
        "credentials for target DB password"
    ],
    "target_db_name": [
        "the name of your target database",
        "target DB schema name",
        "destination database name",
        "database you are loading into"
    ]
}


# ----------------------------
# Qdrant client
# ----------------------------
client = QdrantClient(
    url=QDRANT_HOST,
    api_key=QDRANT_API_KEY,
)

embedder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

collection_name = "chat_history"

# client.delete_collection(collection_name="chat_history")
client.recreate_collection(
    collection_name=collection_name,
    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
    on_disk_payload=True
)


client.create_payload_index(
    collection_name=collection_name,
    field_name="user_id",
    field_schema=PayloadSchemaType.KEYWORD
)

client.create_payload_index(
    collection_name=collection_name,
    field_name="session_id",
    field_schema=PayloadSchemaType.KEYWORD
)


def store_message(user_id: str, session_id: str, role: str, message: str):
    embedding = embedder.encode(message).tolist()
    point = PointStruct(
        id=str(uuid.uuid4()),  # unique ID per message
        vector=embedding,
        payload={
            "user_id": str(user_id),
            "session_id": str(session_id),
            "role": str(role),
            "message": str(message),
            "timestamp": str(time.time())
        }
    )
    client.upsert(collection_name=collection_name, points=[point], wait=True)


def get_session_history(user_id: str, session_id: str, limit: int = 10):
    user_id = str(user_id)
    session_id = str(session_id)

    scroll_filter = Filter(
        must=[
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
            FieldCondition(key="session_id", match=MatchValue(value=session_id))
        ]
    )

    try:
        results, next = client.scroll(
            collection_name=collection_name,
            limit=limit,
            scroll_filter=scroll_filter,
            with_payload=True
        )
    except Exception as e:
        print(f"Error during scroll: {e}")
        return []

    if not results:
        return []
    return [r.payload['message'] for r in results]


def embed(text: str):
    return embedder.encode(text).tolist()


# ----------------------------
# Setup schema with paraphrases
# ----------------------------
def setup_schema(collection_name="field_collection"):
    first_vec = embed(list(fields_with_phrases.values())[0][0])
    client.delete_collection(collection_name=collection_name)
    client.recreate_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=len(first_vec), distance=Distance.COSINE)
    )

    points = []
    for key, phrases in fields_with_phrases.items():
        for phrase in phrases:
            points.append(
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector=embed(phrase),
                    payload={"field": key, "description": phrase}
                )
            )

    client.upsert(collection_name=collection_name, points=points)


def detect_field(user_input: str):
    vector = embed(user_input)
    results = client.search(
        collection_name="field_collection",
        query_vector=vector,
        limit=1,
        with_payload=True
    )
    if results:
        best_match = results[0]
        print(f"Best match: {best_match.payload['field']} | Score: {best_match.score}")
        return best_match.payload["field"], best_match.score
    return None, 0.0


def extract_value(user_input: str, field: str):
    if field == "databricks_host_url":
        match = re.search(r"(https?://\S+|[a-zA-Z0-9.-]+\.[a-z]{2,})", user_input)
        return match.group(0) if match else user_input
    if field in ["idmc_username", "source_db_username", "target_db_username"]:
        match = re.search(r"[\w\.-]+@[\w\.-]+|\w+", user_input)
        return match.group(0) if match else user_input
    if field in ["idmc_password", "source_db_password", "target_db_password", "databricks_token"]:
        return user_input.strip()
    if field in ["source_platform", "mapping_type"]:
        return user_input.strip()
    return user_input.strip()


state = {"collected_info": {k: None for k in fields_with_phrases}}


def process_input(user_input: str):
    try:
        field, score = detect_field(user_input)
        if field and score > 0.6:
            value = extract_value(user_input, field)
            state["collected_info"][field] = value
            print(f"✅ Detected field: {field} | Value: {value}")
        else:
            print("⚠️ No relevant field detected")
    except Exception as e:
        print(f"Error processing input: {e}")


# initialize schema with paraphrases
# setup_schema("field_collection")
