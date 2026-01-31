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

        ctx.dbg(f"Creating filestore {display_name} in Gemini...")
        result = g_client.file_search_stores.create(config={"display_name": display_name})
        ctx.dbg(result or None)
        if result:
            filestore.update(
                {
                    "name": result.name,
                    "displayName": result.display_name,
                    "createTime": result.create_time,
                    "updateTime": result.update_time,
                    "activeDocumentsCount": result.active_documents_count,
                    "pendingDocumentsCount": result.pending_documents_count,
                    "failedDocumentsCount": result.failed_documents_count,
                    "sizeBytes": result.size_bytes,
                }
            )
            id = await g_db.create_filestore_async(filestore, user=user)
            row = g_db.get_filestore(id, user=user)
        else:
            raise Exception("Failed to create filestore in Gemini")

        return web.json_response(filestore_dto(row) if row else "")

    ctx.add_post("filestores", create_filestore)

    async def delete_filestore(request):
        id = request.match_info["id"]
        user = ctx.get_username(request)
        row = g_db.get_filestore(id, user=user)
        if not row:
            raise Exception("Filestore does not exist")

        name = row.get("name")
        if name:
            ctx.dbg(f"Deleting filestore {name} in Gemini...")
            g_client.file_search_stores.delete(name=name, config={"force": True})
        else:
            ctx.dbg(f"Filestore {id} has no name, skipping Gemini deletion...")

        ctx.dbg(f"Filestore {name} deleted in Gemini, removing local record...")
        g_db.delete_filestore(id, user=user)
        return web.json_response({})

    ctx.add_delete("filestores/{id}", delete_filestore)

    async def upload_to_filestore(request):
        user = ctx.get_username(request)
        id = request.match_info["id"]
        ctx.log(f"upload_to_filestore {id} {user if user else ''}")
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
                    "filename": save_filename,
                    "url": url,
                    "hash": sha256_hash,
                    "size": len(content),
                    "displayName": filename,
                    "mimeType": mimetype,
                    "filestoreId": int(id),
                    "category": category,
                },
                user=user,
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

    async def upload_document(request):
        user = ctx.get_username(request)
        id = int(request.match_info["id"])
        doc = g_db.get_document(int(id), user=user)
        if not doc:
            raise Exception("Document does not exist")

        if doc.get("name"):
            try:
                ctx.dbg(f"Deleting existing document {doc.get('name')} from filestore...")
                g_client.file_search_stores.documents.delete(name=doc.get("name"), config={"force": True})
            except Exception as e:
                ctx.err(f"Could not delete document {doc.get('name')}", e)

        await g_db.update_document_async(id, {"error": None, "uploadedAt": None}, user=user)
        g_worker.start()
        while g_worker.running:
            await asyncio.sleep(2)
            doc = g_db.get_document(id, user=user)
            if doc.get("uploadedAt") or doc.get("error"):
                return web.json_response(document_dto(doc))

        return web.json_response(document_dto(doc))

    ctx.add_post("documents/{id}/upload", upload_document)

    async def sync_filestore_documents(request):
        id = request.match_info["id"]
        user = ctx.get_username(request)
        filestore = g_db.get_filestore(int(id), user=user)
        if not filestore:
            raise Exception("Filestore does not exist")

        # Build hash lookup for all local documents
        local_doc_hashes = {}
        local_doc_names = {}
        local_docs = []
        for doc in g_db.query_documents_all({"filestoreId": int(id)}, user=user):
            local_docs.append(doc)
            local_doc_hashes[doc.get("hash")] = doc
            local_doc_names[doc.get("name")] = doc

        ctx.log(f"Found {len(local_docs)} local documents in database")
        ctx.log(f"Local hashes available: {len(local_doc_hashes)}")

        local_missing = []
        remote_missing = []
        missing_metadata = []
        metadata_mismatch = []
        unmatched = []
        hash_counts = {}

        def extract_custom_metadata(doc):
            remote_id = None
            remote_hash = None
            if doc.custom_metadata:
                for item in doc.custom_metadata:
                    if item.key == "id" and item.numeric_value:
                        remote_id = int(item.numeric_value)
                    elif item.key == "hash" and item.string_value:
                        remote_hash = item.string_value
            return remote_id, remote_hash

        pager = g_client.file_search_stores.documents.list(parent=filestore.get("name"))

        # Track which remote documents we've seen (by hash)
        seen_remote_hashes = set()

        # Track stats for debugging
        matched_by_hash = 0
        remote_docs = 0

        # Extract documents from the result
        for doc in pager:
            remote_docs += 1
            remote_id, remote_hash = extract_custom_metadata(doc)

            # Match by hash or name
            local_doc = local_doc_hashes.get(remote_hash) if remote_hash else local_doc_names.get(doc.name)
            info = f"name={doc.name}, display={doc.display_name}, size={doc.size_bytes}, hash={remote_hash}"
            doc_context = {"doc": doc, "local": local_doc}

            if not local_doc:
                local_missing.append(doc)
                ctx.dbg(f"Remote doc not found locally: ")
                continue

            if not remote_hash or not remote_id:
                missing_metadata.append(doc_context)
                ctx.dbg(f"Remote doc missing metadata: {info}")
                continue

            seen_remote_hashes.add(remote_hash)
            matched_by_hash += 1

            # Update local doc with remote name if missing
            new_dto = {
                "name": doc.name,
                "displayName": doc.display_name,
                "sizeBytes": doc.size_bytes,
                "mimeType": doc.mime_type,
                "createTime": doc.create_time.isoformat(" ") if doc.create_time else None,
                "updateTime": doc.update_time.isoformat(" ") if doc.update_time else None,
                "state": doc.state,
                "customMetadata": json.dumps(g_db.custom_metadata_dto(doc.custom_metadata)),
            }
            unmatched_fields = []
            for key, value in new_dto.items():
                local_value = local_doc.get(key)
                if local_value != value:
                    unmatched_fields.append(key)

            if len(unmatched_fields) > 0:
                ctx.dbg(
                    f"Updating local doc {local_doc.get('category')}/{local_doc.get('displayName')} unmatched fields: {unmatched_fields}"
                )
                unmatched.append(doc_context)
                await g_db.update_document_async(local_doc.get("id"), new_dto, user=user)

            # Verify that remote_id matches the local document id
            if local_doc.get("id") != remote_id or local_doc.get("hash") != remote_hash:
                # Metadata id doesn't match the document with this hash
                ctx.dbg(
                    f"Metadata mismatch: id={local_doc.get('id')}|{remote_id}, hash={local_doc.get('hash')}|{remote_hash}"
                )
                metadata_mismatch.append(doc_context)

            # Track hash occurrences to detect duplicates
            if remote_hash:
                hash_counts[remote_hash] = hash_counts.get(remote_hash, 0) + 1

        # Find local documents that don't exist in remote
        for local_doc in local_docs:
            local_hash = local_doc.get("hash")
            if local_hash and local_hash not in seen_remote_hashes:
                remote_missing.append(local_doc)

        total_remote = matched_by_hash + len(local_missing)

        hashes_with_duplicates = [h for h, count in hash_counts.items() if count > 1]
        duplicate_docs = []
        for hash in hashes_with_duplicates:
            doc = local_doc_hashes[hash]
            duplicate_docs.append(doc)

        for d in remote_missing:
            g_db.update_document(d.get("id"), {"state": "MISSING_FROM_REMOTE"}, user=user)
        for d in missing_metadata:
            local_doc = d.get("doc")
            g_db.update_document(local_doc.get("id"), {"state": "MISSING_METADATA"}, user=user)
        for d in metadata_mismatch:
            local_doc = d.get("doc")
            g_db.update_document(local_doc.get("id"), {"state": "METADATA_MISMATCH"}, user=user)
        for d in duplicate_docs:
            g_db.update_document(d.get("id"), {"state": "DUPLICATE_FILE"}, user=user)

        ctx.log(
            f"Sync complete: total_remote={total_remote}, local_docs={len(local_docs)}, matched={matched_by_hash}, missing_metadata={len(missing_metadata)}, unmatched={len(local_missing)}"
        )

        def doc_filename(doc):
            if isinstance(doc, dict):
                return f"{doc.get('category')}/{doc.get('displayName')}"
            else:
                category = None
                for meta in doc.custom_metadata or []:
                    if meta.key == "category" and meta.string_value:
                        category = meta.string_value
                        return f"{category}/{doc.display_name}"
                return doc.display_name

        return web.json_response(
            {
                "Missing from Local": {
                    "count": len(local_missing),
                    "docs": [doc_filename(d) for d in local_missing[:5]],
                },
                "Missing from Gemini": {
                    "count": len(remote_missing),
                    "docs": [doc_filename(d) for d in remote_missing[:5]],
                },
                "Missing Metadata": {
                    "count": len(missing_metadata),
                    "docs": [doc_filename(d.get("doc")) for d in missing_metadata[:5]],
                },
                "Metadata Mismatch": {
                    "count": len(metadata_mismatch),
                    "docs": [doc_filename(d.get("doc")) for d in metadata_mismatch[:5]],
                },
                "Unmatched Fields": {
                    "count": len(unmatched),
                    "docs": [doc_filename(d.get("doc")) for d in unmatched[:5]],
                },
                "Duplicate Documents": {
                    "count": len(duplicate_docs),
                    "docs": [doc_filename(d) for d in duplicate_docs[:5]],
                },
                "Summary": {
                    "Local Documents": len(local_docs),
                    "Remote Documents": remote_docs,
                    "Matched Documents": matched_by_hash,
                },
            }
        )

    ctx.add_post("filestores/{id}/sync", sync_filestore_documents)

    # Start the upload worker to check for pending uploads
    try:
        g_worker.start()
    except Exception as e:
        ctx.err("Failed to start UploadWorker", e)


__install__ = install
