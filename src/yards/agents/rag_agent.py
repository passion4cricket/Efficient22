import os
import glob
import pandas as pd

from langchain_community.document_loaders import (
    PyPDFLoader,
    UnstructuredWordDocumentLoader,
)
from langchain_community.document_loaders.sql_database import SQLDatabaseLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.utilities import SQLDatabase
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain.schema import Document
from yards.utils.utils import get_base_dir


class RagAgent:
    def __init__(self, source_paths=None, db_config=None, chroma_dir="/chroma_storage"):
        base_dir = get_base_dir()
        self.chroma_dir = rf"{base_dir}{chroma_dir}"

        if source_paths is None:
            source_paths = glob.glob(f"{base_dir}/assets/rag/*")
            print(f"Loading all files from assets: {source_paths}")

        self.source_paths = source_paths
        self.db_config = db_config

        self.embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
        self.chunks = self._load_all_sources()
        self.vectorstore = self._get_vectorstore()


    def _load_all_sources(self):
        all_chunks = []
        splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)

        for path in self.source_paths:
            if not os.path.exists(path):
                continue

            ext = os.path.splitext(path)[1].lower()

            if ext == ".pdf":
                loader = PyPDFLoader(path)
                docs = loader.load()
            elif ext in [".doc", ".docx"]:
                loader = UnstructuredWordDocumentLoader(path)
                docs = loader.load()            
            elif ext == ".csv":
                docs = self._load_csv(path)
            elif ext in [".xlsx", ".xls"]:
                docs = self._load_excel(path)
            else:
                print(f"Skipping unsupported file: {path}")
                continue

            chunks = splitter.split_documents(docs)

            for c in chunks:
                c.metadata["source"] = os.path.basename(path)
                c.metadata["type"] = ext
            all_chunks.extend(chunks)

        if self.db_config:
            db_loader = self._load_from_database()
            all_chunks.extend(db_loader)

        if not all_chunks:
            raise ValueError("No valid data sources found.")

        return all_chunks


    def _load_csv(self, path):
        df = pd.read_csv(path)
        docs = []
        for _, row in df.iterrows():
            content = "\n".join([f"{col}: {row[col]}" for col in df.columns])
            docs.append(Document(page_content=content, metadata={"filename": os.path.basename(path)}))
        return docs
    

    def _load_excel(self, path):
        dfs = pd.read_excel(path, sheet_name=None)
        docs = []
        for sheet_name, df in dfs.items():
            for _, row in df.iterrows():
                content = "\n".join([f"{col}: {row[col]}" for col in df.columns])
                docs.append(Document(page_content=content, metadata={
                    "filename": os.path.basename(path),
                    "sheet": sheet_name
                }))
        return docs
    

    def _load_from_database(self):
        db_uri = (
            f"mysql+pymysql://{self.db_config['user']}:{self.db_config['password']}"
            f"@{self.db_config['host']}/{self.db_config['database']}"
        )
        db = SQLDatabase.from_uri(db_uri)

        loader = SQLDatabaseLoader(db, query="SELECT * FROM your_table_name;")
        docs = loader.load()
        splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
        chunks = splitter.split_documents(docs)

        for c in chunks:
            c.metadata["source"] = self.db_config["database"]
            c.metadata["type"] = "database"
        return chunks
    

    def _get_vectorstore(self):
        if not os.path.exists(self.chroma_dir) or not os.listdir(self.chroma_dir):
            return Chroma.from_documents(
                documents=self.chunks,
                embedding=self.embeddings,
                persist_directory=self.chroma_dir
            )
        else:
            return Chroma(
                embedding_function=self.embeddings,
                persist_directory=self.chroma_dir
            )
            

    def retrieve(self, query, k=3):
        if len(self.chunks) <= k:
            docs = self.chunks
        else:
            docs = self.vectorstore.similarity_search(query, k=k)
        return [d.page_content for d in docs]