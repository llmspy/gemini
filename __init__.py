import asyncio
import hashlib
import json
import mimetypes
import os
import time

from aiohttp import web
from google import genai
from google.genai.errors import ClientError

from .db import GeminiDB
from .upload_worker import UploadWorker

g_db = None
g_client = None
g_worker = None


def install(ctx):
    global g_client, g_worker

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        ctx.log("GEMINI_API_KEY is not configured")
        ctx.disabled = True
        return

    def get_db():
        global g_db
        if g_db is None and GeminiDB:
            try:
                db_path = os.path.join(ctx.get_user_path(), "gemini", "gemini.sqlite")
                g_db = GeminiDB(ctx, db_path)
                ctx.register_shutdown_handler(g_db.db.close)
            except Exception as e:
                ctx.err("Failed to init GeminiDB", e)
        return g_db

    if not get_db():
        return

    g_client = genai.Client(api_key=api_key)
    g_worker = UploadWorker(ctx, g_db, g_client)

    def filestore_dto(row):
        return row and g_db.to_dto(row, ["metadata"])

    def document_dto(row):
        return row and g_db.to_dto(row, ["metadata", "customMetadata"])

    async def query_filestores(request):
        rows = g_db.query_filestores(request.query, user=ctx.get_username(request))
        dtos = [filestore_dto(row) for row in rows]
        return web.json_response(dtos)

    ctx.add_get("filestores", query_filestores)

    async def create_filestore(request):
        user = ctx.get_username(request)
        filestore = await request.json()
        display_name = filestore.get("displayName")
        if not display_name:
            raise Exception("displayName is required")
        id = await g_db.create_filestore_async(filestore, user=user)
        row = g_db.get_filestore(id, user=user)
        try:
            result = g_client.file_search_stores.create(config={"display_name": display_name})
            ctx.dbg("g_client.file_search_stores.create")
            ctx.dbg(result or None)
            if result:
                await g_db.update_filestore_async(
                    id,
                    {
                        "name": result.name,
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
            await g_db.update_filestore(id, {"error": ctx.error_message(e)}, user=user)

        return web.json_response(filestore_dto(row) if row else "")

    ctx.add_post("filestores", create_filestore)

    async def delete_filestore(request):
        id = request.match_info["id"]
        user = ctx.get_username(request)
        row = g_db.get_filestore(id, user=user)
        if not row:
            raise Exception("Filestore does not exist")

        name = row.get("name")
        g_client.file_search_stores.delete(name=name, config={"force": True})

        g_db.delete_filestore(id, user=user)
        return web.json_response({})

    ctx.add_delete("filestores/{id}", delete_filestore)

    async def upload_to_filestore(request):
        ctx.log("upload_to_filestore")
        user = ctx.get_username(request)
        id = request.match_info["id"]
        category = request.query.get("category")

        filestore = g_db.get_filestore(id, user=user)
        if not filestore:
            raise Exception("Filestore does not exist")

        tasks = []
        reader = await request.multipart()

        field = await reader.next()
        while field:
            if field.name != "file" and not field.name.startswith("file"):
                field = await reader.next()
                continue

            if not field.filename:
                field = await reader.next()
                continue

            filename = field.filename
            content = await field.read()
            mimetype = ctx.get_file_mime_type(filename)

            # Calculate SHA256
            sha256_hash = hashlib.sha256(content).hexdigest()
            ext = filename.rsplit(".", 1)[1] if "." in filename else ""
            if not ext:
                ext = mimetypes.guess_extension(mimetype) or ""
                if ext.startswith("."):
                    ext = ext[1:]

            if not ext:
                ext = "bin"

            save_filename = f"{sha256_hash}.{ext}" if ext else sha256_hash

            # Use first 2 chars for subdir to avoid too many files in one dir
            subdir = sha256_hash[:2]
            relative_path = f"{subdir}/{save_filename}"
            full_path = ctx.get_cache_path(relative_path)
            url = f"/~cache/{relative_path}"

            existing_doc = g_db.find_document({"hash": sha256_hash}, user=user)
            if existing_doc:
                # If doc exists delete document and create new
                document_name = existing_doc.get("name")
                try:
                    ctx.dbg(f"Deleting existing document {document_name} from filestore...")
                    g_client.file_search_stores.documents.delete(name=document_name, config={"force": True})
                except Exception as e:
                    ctx.err(f"Could not delete document {document_name}", e)
                g_db.delete_document(existing_doc.get("id"), user=user)

            # New document
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            with open(full_path, "wb") as f:
                f.write(content)

            info = {
                "date": int(time.time()),
                "url": url,
                "size": len(content),
                "type": mimetype,
                "name": filename,
            }
            # Save metadata
            info_path = os.path.splitext(full_path)[0] + ".info.json"
            with open(info_path, "w") as f:
                json.dump(info, f)

            task = g_db.create_document_async(
                {
                    "user": user,
                    "filename": save_filename,
                    "url": url,
                    "hash": sha256_hash,
                    "size": len(content),
                    "displayName": filename,
                    "mimeType": mimetype,
                    "filestoreId": int(id),
                    "category": category,
                }
            )
            tasks.append(asyncio.create_task(task))

            field = await reader.next()

        # wait all tasks
        doc_ids = await asyncio.gather(*tasks) if tasks else []
        docs = g_db.query_documents({"ids_in": doc_ids}, user=user)

        g_worker.start()

        return web.json_response(docs)

    ctx.add_post("filestores/{id}/upload", upload_to_filestore)

    async def query_documents(request):
        rows = g_db.query_documents(request.query, user=ctx.get_username(request))
        dtos = [document_dto(row) for row in rows]
        return web.json_response(dtos)

    ctx.add_get("documents", query_documents)

    async def delete_document(request):
        id = request.match_info["id"]
        user = ctx.get_username(request)
        row = g_db.get_document(id, user=user)
        if not row:
            raise Exception("Document does not exist")

        try:
            g_client.file_search_stores.documents.delete(name=row.get("name"), config={"force": True})
        except ClientError as e:
            if e.code == 404:
                ctx.dbg(f"Document {row.get('name')} already deleted in Gemini")
            else:
                raise Exception(f"Could not delete document {row.get('name')}: {e.message or e.status}")

        g_db.delete_document(id, user=user)
        return web.json_response({})

    ctx.add_delete("documents/{id}", delete_document)

    def doc_to_dto(doc):
        # Extract serializable dict from the document result
        return {
            "name": doc.name,
            "displayName": doc.display_name,
            "mimeType": doc.mime_type,
            "sizeBytes": doc.size_bytes,
            "createTime": doc.create_time.isoformat(),
            "updateTime": doc.update_time.isoformat(),
            "state": doc.state,
            "customMetadata": g_db.custom_metadata_dto(doc.custom_metadata),
        }

    async def filestore_documents(request):
        id = request.match_info["id"]
        user = ctx.get_username(request)
        filestore = g_db.get_filestore(int(id), user=user)

        if not filestore:
            raise Exception("Filestore does not exist")

        # Call Gemini API to list documents
        pager = g_client.file_search_stores.documents.list(parent=filestore.get("name"))
        documents = []
        for doc in pager:
            documents.append(doc_to_dto(doc))
        return web.json_response(documents)

    ctx.add_get("filestores/{id}/documents", filestore_documents)

    async def filestore_categories(request):
        id = request.match_info["id"]
        user = ctx.get_username(request)
        categories = g_db.document_categories(int(id), user=user)
        return web.json_response(categories)

    ctx.add_get("filestores/{id}/categories", filestore_categories)


__install__ = install
