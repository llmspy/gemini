import os
from datetime import datetime
from typing import Any, Dict

from llms.db import DbManager, order_by, select_columns, to_dto, valid_columns


def with_user(data, user):
    if user is None:
        if "user" in data:
            del data["user"]
        return data
    else:
        data["user"] = user
        return data


def to_ints(ints):
    ret = []
    if isinstance(ints, (str)):
        ints_str = ints.split(",")
        for int_str in ints_str:
            ret.append(int(int_str))
    elif isinstance(ints, (list)):
        return ints
    return ret


class GeminiDB:
    def __init__(self, ctx, db_path=None, clone=None):
        if db_path is None:
            raise Exception("db_path is required")

        self.ctx = ctx
        self.db_path = str(db_path)
        dirname = os.path.dirname(self.db_path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)

        self.db = DbManager(ctx, self.db_path) if not clone else DbManager(ctx, self.db_path, clone=clone.db)
        self.columns = {
            "filestore": {
                "id": "INTEGER",
                "user": "TEXT",
                "createdAt": "TIMESTAMP",
                "updatedAt": "TIMESTAMP",
                "name": "TEXT",
                "displayName": "TEXT",
                "createTime": "TEXT",
                "updateTime": "TEXT",
                "activeDocumentsCount": "INTEGER",
                "pendingDocumentsCount": "INTEGER",
                "failedDocumentsCount": "INTEGER",
                "sizeBytes": "INTEGER",
                "metadata": "JSON",
                "error": "TEXT",
                "ref": "TEXT",
            },
            "document": {
                "id": "INTEGER",
                "filestoreId": "INTEGER",
                "user": "TEXT",
                "createdAt": "TIMESTAMP",
                "filename": "TEXT",
                "url": "TEXT",  # /~cache/23/238841878a0ebeeea8d0034cfdafc82b15d3a6d00c344b0b5e174acbb19572ef.png
                "hash": "TEXT",  # 238841878a0ebeeea8d0034cfdafc82b15d3a6d00c344b0b5e174acbb19572ef
                "size": "INTEGER",  # 1593817 (bytes)
                # https://ai.google.dev/api/file-search/documents
                "displayName": "TEXT",
                "name": "TEXT",
                "customMetadata": "JSON",  # [{object (CustomMetadata)}]
                "createTime": "TEXT",
                "updateTime": "TEXT",
                "sizeBytes": "INTEGER",
                "mimeType": "TEXT",  # text/markdown, application,pdf
                "state": "TEXT",  # STATE_UNSPECIFIED, STATE_PENDING, STATE_ACTIVE, STATE_FAILED
                "category": "text",  # folder name
                "tags": "JSON",  # {"bug": 0.9706085920333862, "mask": 0.9348311424255371, "glowing": 0.8394700884819031}
                "startedAt": "TIMESTAMP",
                "uploadedAt": "TIMESTAMP",
                "metadata": "JSON",
                "error": "TEXT",
                "ref": "TEXT",
            },
        }
        if not clone:
            with self.db.create_writer_connection() as conn:
                self.init_db(conn)

    def clone(self):
        return GeminiDB(self.ctx, self.db_path, clone=self)

    # Check for missing columns and migrate if necessary
    def add_missing_columns(self, conn, table):
        cur = self.db.exec(conn, f"PRAGMA table_info({table})")
        columns = {row[1] for row in cur.fetchall()}

        for col, dtype in self.columns[table].items():
            if col not in columns:
                try:
                    self.db.exec(conn, f"ALTER TABLE {table} ADD COLUMN {col} {dtype}")
                except Exception as e:
                    self.ctx.err(f"adding {table} column {col}", e)

    def init_db(self, conn):
        # Create table with all columns
        # Note: default SQLite timestamp has different tz to datetime.now()
        overrides = {
            "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
            "createdAt": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "updatedAt": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        }
        sql_columns = ",".join(
            [f"{col} {overrides.get(col, dtype)}" for col, dtype in self.columns["filestore"].items()]
        )
        self.db.exec(
            conn,
            f"""
            CREATE TABLE IF NOT EXISTS filestore (
                {sql_columns},
                CONSTRAINT uniq_displayname UNIQUE (user,displayName)
            )
            """,
        )
        self.add_missing_columns(conn, "filestore")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_filestore_user ON filestore(user)")
        self.db.exec(
            conn,
            "CREATE INDEX IF NOT EXISTS idx_filestore_createdat ON filestore(createdAt)",
        )
        self.db.exec(
            conn,
            "CREATE INDEX IF NOT EXISTS idx_filestore_updatedat ON filestore(updatedAt)",
        )

        sql_columns = ",".join(
            [f"{col} {overrides.get(col, dtype)}" for col, dtype in self.columns["document"].items()]
        )
        self.db.exec(
            conn,
            f"""
            CREATE TABLE IF NOT EXISTS document (
                {sql_columns},
                CONSTRAINT uniq_filestoreid_hash UNIQUE (filestoreId,hash)
            )
            """,
        )
        self.add_missing_columns(conn, "document")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_document_user ON document(user)")
        self.db.exec(
            conn,
            "CREATE INDEX IF NOT EXISTS idx_document_createdat ON document(createdAt)",
        )

    def to_dto(self, row, json_columns):
        return to_dto(self.ctx, row, json_columns)

    def get_user_filter(self, user=None, params=None):
        if user is None:
            return "WHERE user IS NULL", params or {}
        else:
            args = params.copy() if params else {}
            args.update({"user": user})
            return "WHERE user = :user", args

    def sql_filter(self, all_columns, query: Dict[str, Any], args: Dict[str, Any] = None, user=None):
        # always filter by user
        sql_where, params = self.get_user_filter(user, args)

        filter = {}
        for k in query:
            if k in all_columns:
                filter[k] = query[k]
                params[k] = query[k]

        if len(filter) > 0:
            sql_where += " AND " + " AND ".join([f"{k} = :{k}" for k in filter])

        return sql_where, params

    def get_filestore(self, id, user=None):
        try:
            sql_where, params = self.get_user_filter(user, {"id": id})
            return self.db.one(f"SELECT * FROM filestore {sql_where} AND id = :id", params)
        except Exception as e:
            self.ctx.err(f"get_filestore ({id}, {user})", e)
            return None

    def query_filestores(self, query: Dict[str, Any], user=None):
        try:
            table = "filestore"
            columns = self.columns[table]
            all_columns = columns.keys()

            take = min(int(query.get("take", "50")), 1000)
            skip = int(query.get("skip", "0"))
            sort = query.get("sort", "-id")

            # always filter by user
            sql_where, params = self.get_user_filter(user, {"take": take, "skip": skip})

            filter = {}
            for k in query:
                if k in all_columns:
                    filter[k] = query[k]
                    params[k] = query[k]

            if len(filter) > 0:
                sql_where += " AND " + " AND ".join([f"{k} = :{k}" for k in filter])

            if "null" in query:
                cols = valid_columns(all_columns, query["null"])
                if len(cols) > 0:
                    sql_where += " AND " + " AND ".join([f"{k} IS NULL" for k in cols])

            if "not_null" in query:
                cols = valid_columns(all_columns, query.get("not_null"))
                if len(cols) > 0:
                    sql_where += " AND " + " AND ".join([f"{k} IS NOT NULL" for k in cols])

            if "q" in query:
                sql_where += " AND " if sql_where else "WHERE "
                sql_where += "(displayName LIKE :q)"
                params["q"] = f"%{query['q']}%"

            sql = f"{select_columns(all_columns, query.get('fields'), select=query.get('select'))} FROM {table} {sql_where} {order_by(all_columns, sort)} LIMIT :take OFFSET :skip"

            if query.get("as") == "column":
                return self.db.column(sql, params)
            else:
                return self.db.all(sql, params)

        except Exception as e:
            self.ctx.err(f"query_filestores ({take}, {skip})", e)
            return []

    def prepare_filestore(self, filestore, id=None, user=None):
        now = datetime.now()
        if id:
            filestore["id"] = id
        else:
            filestore["createdAt"] = now
        filestore["updatedAt"] = now
        return with_user(filestore, user=user)

    def create_filestore(self, filestore: Dict[str, Any], user=None):
        return self.db.insert(
            "filestore",
            self.columns["filestore"],
            self.prepare_filestore(filestore, user=user),
        )

    async def create_filestore_async(self, filestore: Dict[str, Any], user=None):
        return await self.db.insert_async(
            "filestore",
            self.columns["filestore"],
            self.prepare_filestore(filestore, user=user),
        )

    def update_filestore(self, id, filestore: Dict[str, Any], user=None):
        return self.db.update(
            "filestore",
            self.columns["filestore"],
            self.prepare_filestore(filestore, id, user=user),
        )

    async def update_filestore_async(self, id, filestore: Dict[str, Any], user=None):
        return await self.db.update_async(
            "filestore",
            self.columns["filestore"],
            self.prepare_filestore(filestore, id, user=user),
        )

    def delete_filestore(self, id, user=None, callback=None):
        sql_where, params = self.get_user_filter(user, {"id": id})
        self.db.write(f"DELETE FROM document {sql_where} AND filestoreId = :id", params, callback)
        self.db.write(f"DELETE FROM filestore {sql_where} AND id = :id", params, callback)

    def query_documents(self, query: Dict[str, Any], user=None):
        try:
            table = "document"
            columns = self.columns[table]
            all_columns = columns.keys()

            take = min(int(query.get("take", "50")), 1000)
            skip = int(query.get("skip", "0"))
            sort = query.get("sort", "-id")

            sql_where, params = self.sql_filter(all_columns, query, args={"take": take, "skip": skip}, user=user)

            if "null" in query:
                cols = valid_columns(all_columns, query["null"])
                if len(cols) > 0:
                    sql_where += " AND " + " AND ".join([f"{k} IS NULL" for k in cols])

            if "not_null" in query:
                cols = valid_columns(all_columns, query.get("not_null"))
                if len(cols) > 0:
                    sql_where += " AND " + " AND ".join([f"{k} IS NOT NULL" for k in cols])

            ids_in = query.get("ids_in")
            if ids_in:
                ids = to_ints(ids_in)
                id_params = {}
                if len(ids) > 0:
                    i = 0
                    for id in ids:
                        id_params[f"id_{i}"] = id
                        i = i + 1
                    sql_where += " AND id IN (" + ",".join([f":{p}" for p in id_params]) + ")"
                    params.update(id_params)

            displaynames_in = query.get("displayNames")
            if displaynames_in:
                names = displaynames_in if isinstance(displaynames_in, list) else displaynames_in.split(",")
                name_params = {}
                if len(names) > 0:
                    i = 0
                    for name in names:
                        name_params[f"name_{i}"] = name
                        i = i + 1
                    sql_where += " AND displayName IN (" + ",".join([f":{p}" for p in name_params]) + ")"
                    params.update(name_params)

            if "q" in query:
                sql_where += " AND " if sql_where else "WHERE "
                sql_where += "(displayName LIKE :q)"
                params["q"] = f"%{query['q']}%"

            if sort == "uploading":
                sql_order_by = "ORDER BY CASE WHEN uploadedAt IS NULL AND error IS NULL THEN createdAt ELSE '9999-12-31' END, uploadedAt DESC"
            else:
                sql_order_by = order_by(all_columns, sort)
            

            sql = f"{select_columns(all_columns, query.get('fields'), select=query.get('select'))} FROM {table} {sql_where} {sql_order_by} LIMIT :take OFFSET :skip"

            if query.get("as") == "column":
                return self.db.column(sql, params)
            else:
                return self.db.all(sql, params)

        except Exception as e:
            self.ctx.err(f"query_documents ({take}, {skip})", e)
            return []

    def get_document(self, id, user=None):
        sql_where, params = self.get_user_filter(user, {"id": id})
        return self.db.one(f"SELECT * FROM document {sql_where} AND id = :id", params)

    def find_document(self, query, user=None):
        sql_where, params = self.sql_filter(self.columns["document"].keys(), query, user=user)
        return self.db.one(f"SELECT * FROM document {sql_where} LIMIT 1", params)

    def prepare_document(self, document, id=None, user=None):
        now = datetime.now()
        if id:
            document["id"] = id
        else:
            document["createdAt"] = now
        document["updatedAt"] = now
        return with_user(document, user=user)

    def create_document(self, document: Dict[str, Any], user=None, callback=None):
        return self.db.insert(
            "document",
            self.columns["document"],
            self.prepare_document(document, user=user),
            callback=callback,
        )

    async def create_document_async(self, document: Dict[str, Any], user=None):
        return await self.db.insert_async(
            "document",
            self.columns["document"],
            self.prepare_document(document, user=user),
        )

    def update_document(self, id, document: Dict[str, Any], user=None):
        return self.db.update(
            "document",
            self.columns["document"],
            self.prepare_document(document, id, user=user),
        )

    async def update_document_async(self, id, document: Dict[str, Any], user=None):
        return await self.db.update_async(
            "document",
            self.columns["document"],
            self.prepare_document(document, id, user=user),
        )

    def get_pending_documents(self, limit=10):
        try:
            return self.db.all(f"SELECT * FROM document WHERE uploadedAt IS NULL AND error IS NULL LIMIT {limit}")
        except Exception as e:
            self.ctx.err("get_pending_documents", e)
            return []

    def delete_document(self, id, user=None, callback=None):
        sql_where, params = self.get_user_filter(user, {"id": id})
        self.db.write(f"DELETE FROM document {sql_where} AND id = :id", params, callback)

    def document_categories(self, id, user=None):
        sql_where, params = self.get_user_filter(user, {"id": id})
        return self.db.all(
            f"SELECT IFNULL(category, '') AS category, COUNT(*) as count, SUM(size) AS size FROM document {sql_where} GROUP BY category ORDER BY category",
            params,
        )

    def custom_metadata_dto(self, custom_metadata):
        if custom_metadata is None:
            return None
        ret = []
        for meta in custom_metadata:
            if meta.numeric_value is not None:
                ret.append({"key": meta.key, "numeric_value": meta.numeric_value})
            elif meta.string_list_value is not None:
                ret.append({"key": meta.key, "string_list_value": meta.string_list_value.values})
            elif meta.string_value is not None:
                ret.append({"key": meta.key, "string_value": meta.string_value})
        return ret
