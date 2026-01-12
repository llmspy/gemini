import json
import os
import threading
import time
from datetime import datetime

from google import genai

from .db import GeminiDB

GEMINI_UPLOAD_MIME_TYPES = os.getenv("GEMINI_UPLOAD_MIME_TYPES", 
    "mdx:text/markdown,l:text/markdown,ss:text/markdown,sc:text/markdown")

class UploadWorker:
    def __init__(self, ctx, db: GeminiDB, client: genai.Client):
        self.ctx = ctx
        self.running = False
        self.lock = threading.Lock()
        self.db = (
            db.clone()
        )  # can't share pooled read connections across multiple threads
        self.client = client

        self.include_mime_types = {}
        if GEMINI_UPLOAD_MIME_TYPES:
            for ext_type in GEMINI_UPLOAD_MIME_TYPES.split(","):
                ext_type = ext_type.strip()
                if not ext_type:
                    continue
                ext, mime_type = ext_type.split(":")
                self.include_mime_types[ext] = mime_type

    def start(self):
        with self.lock:
            if self.running:
                return
            self.running = True
            threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        try:
            self.ctx.log("UploadWorker started")
            completed = []
            filestore_ids = set()
            while self.running:
                docs = self.db.get_pending_documents(limit=10)
                if not docs:
                    with self.lock:
                        self.running = False
                    break
                unprocessed_docs = []
                for doc in docs:
                    # don't re-uploaed processed docs (updates may not yet be completed after reads)
                    if doc.get("id") not in completed:
                        unprocessed_docs.append(doc)
                if len(unprocessed_docs) == 0:
                    return

                for doc in unprocessed_docs:
                    if not self.running:
                        break
                    self.process_doc(doc, self.db)
                    completed.append(doc.get("id"))
                    filestore_ids.add(doc.get("filestoreId"))

            # update filestore metadata
            for filestore_id in filestore_ids:
                filestore = self.db.get_filestore(filestore_id)
                if filestore:
                    result = self.client.file_search_stores.get(
                        name=filestore.get("name")
                    )
                    if result:
                        self.db.update_filestore(
                            filestore.get("id"),
                            {
                                "displayName": result.display_name,
                                "createTime": result.create_time,
                                "updateTime": result.update_time,
                                "activeDocumentsCount": result.active_documents_count,
                                "pendingDocumentsCount": result.pending_documents_count,
                                "failedDocumentsCount": result.failed_documents_count,
                                "sizeBytes": result.size_bytes,
                            },
                        )

        except Exception as e:
            self.ctx.err("UploadWorker", e)
        finally:
            with self.lock:
                self.running = False
            self.ctx.log("UploadWorker stopped")

    def process_doc(self, doc, db):
        user = doc.get("user")
        doc_id = doc.get("id")

        try:
            filestore_id = doc.get("filestoreId")
            if not filestore_id:
                raise Exception("Missing filestoreId")

            filestore = db.get_filestore(filestore_id, user=user)
            if not filestore:
                raise Exception("Filestore not found")

            store_name = filestore.get("name")
            if not store_name:
                raise Exception("Filestore has no name (not created in Gemini?)")

            # Resolve file path
            url = doc.get("url")  # /~cache/xx/xxxx.ext
            if not url or not url.startswith("/~cache/"):
                raise Exception("Invalid URL")

            rel_path = url[len("/~cache/") :]
            full_path = self.ctx.get_cache_path(rel_path)

            if not os.path.exists(full_path):
                raise Exception("File not found on disk")

            # Upload
            self.ctx.log(f"Uploading {doc.get('displayName')} to {store_name}")
            db.update_document(doc_id, {"startedAt": datetime.now()}, user=user)

            custom_metadata = [
                {"key": "id", "numeric_value": doc_id},
                {"key": "hash", "string_value": doc.get("hash")},
            ]
            if doc.get("category"):
                custom_metadata.append(
                    {"key": "category", "string_value": doc.get("category")}
                )

            config = {
                "display_name": doc.get("displayName"),
                "custom_metadata": custom_metadata,
                # fails with mime_type application/json, uploading .json succeeds without it
                # "mime_type": doc.get("mimeType"),
            }

            ext = os.path.splitext(full_path)[1].lstrip(".").lower()
            if ext in self.include_mime_types:
                config["mime_type"] = self.include_mime_types[ext]

            if self.ctx.debug:
                self.ctx.dbg(
                    f"Uploading {doc.get('displayName')} to {store_name}\n"
                    + json.dumps(config, indent=2)
                )

            operation = self.client.file_search_stores.upload_to_file_search_store(
                file_search_store_name=store_name,
                file=full_path,
                config=config,
            )

            while not operation.done:
                time.sleep(5)
                operation = self.client.operations.get(operation)

            if operation.error:
                raise Exception(operation.error.message)

            # Update success
            document_name = operation.response.document_name
            db.update_document(
                doc_id,
                {"uploadedAt": datetime.now(), "name": document_name},
                user=user,
            )

            store_doc = self.client.file_search_stores.documents.get(name=document_name)
            db.update_document(
                doc_id,
                {
                    "name": store_doc.name,
                    "displayName": store_doc.display_name,
                    "sizeBytes": store_doc.size_bytes,
                    "mimeType": store_doc.mime_type,
                    "createTime": store_doc.create_time,
                    "updateTime": store_doc.update_time,
                    "state": store_doc.state,
                    "customMetadata": db.custom_metadata_dto(store_doc.custom_metadata),
                },
                user=user,
            )

        except Exception as e:
            self.ctx.err(f"Failed to upload doc {doc.get('id')}", e)
            if doc_id:
                db.update_document(
                    doc_id, {"error": self.ctx.error_message(e)}, user=user
                )
