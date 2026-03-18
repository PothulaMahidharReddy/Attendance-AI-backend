from pymongo import MongoClient
import time

# Server MongoDB
server_client = MongoClient("mongodb://root:mysqlRoot@52.9.12.117:27017/Reports?authSource=admin")
server_db = server_client["Reports"]
server_collection = server_db["biometricdatas"]

# Local MongoDB Atlas
local_client = MongoClient("mongodb+srv://mahidarm96_db_user:fNFLR65zr20I09CC@cluster0.72efjak.mongodb.net/")
local_db = local_client["Reports"]
local_collection = local_db["biometricdatas"]

print("Starting Fast MongoDB Sync...")

# Get last synced id
last_doc = local_collection.find_one(sort=[("_id", -1)])

last_id = last_doc["_id"] if last_doc else None

while True:

    if last_id:
        new_docs = server_collection.find({"_id": {"$gt": last_id}})
    else:
        new_docs = server_collection.find()

    for doc in new_docs:
        local_collection.insert_one(doc)
        last_id = doc["_id"]
        print("Inserted:", doc["_id"])

    time.sleep(5)     