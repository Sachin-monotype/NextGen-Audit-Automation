#!/usr/bin/env python3
"""
Prune MongoDB Audit Collections to keep only the latest N documents per operation.
Usage:
    PYTHONPATH=. python3 scripts/prune_mongo.py --keep 5
"""

import os
import sys
import argparse
from collections import defaultdict
from dotenv import load_dotenv
from pymongo import DeleteOne

load_dotenv('.env')

try:
    from audit_validator.mongo_client import create_mongo_client
except ImportError:
    from backend.app.qa_results_store import _get_collection

def extract_op(d):
    src = d.get('source')
    if isinstance(src, dict) and src.get('operation'):
        return str(src.get('operation'))
    if d.get('operation'):
        return str(d.get('operation'))
    if d.get('event_name'):
        return str(d.get('event_name'))
    if d.get('eventName'):
        return str(d.get('eventName'))
    return 'unclassified'

def prune_mongo(keep_count=5):
    mongo_url = os.getenv('MONGO_DB_URL')
    if not mongo_url:
        print("Error: MONGO_DB_URL environment variable is not set in .env")
        sys.exit(1)

    client = create_mongo_client(mongo_url)

    print("======================================================================")
    print(f"  STARTING MONGO PRUNING (KEEP LATEST {keep_count} PER OPERATION)  ")
    print("======================================================================\n")

    total_pruned_across_all = 0

    for db_name in ['AuditLogsQA', 'AuditLogsPreprod']:
        db = client[db_name]
        print(f">>> Processing Database: [{db_name}]")
        
        for col_name in ['enriched', 'raw', 'dlq']:
            col = db[col_name]
            total_before = col.count_documents({})
            if total_before == 0:
                print(f"  - Collection [{col_name:<10}]: 0 documents (Skipped)")
                continue
                
            docs = list(col.find({}, {'_id': 1, 'source.operation': 1, 'operation': 1, 'event_name': 1, 'eventName': 1}).sort('_id', -1))
            
            op_groups = defaultdict(list)
            for d in docs:
                op = extract_op(d)
                op_groups[op].append(d['_id'])
                
            delete_requests = []
            for op, ids in op_groups.items():
                if len(ids) > keep_count:
                    for doc_id in ids[keep_count:]:
                        delete_requests.append(DeleteOne({'_id': doc_id}))
                        
            if delete_requests:
                chunk_size = 1000
                del_cnt = 0
                for i in range(0, len(delete_requests), chunk_size):
                    chunk = delete_requests[i:i + chunk_size]
                    res = col.bulk_write(chunk)
                    del_cnt += res.deleted_count
                total_pruned_across_all += del_cnt
            else:
                del_cnt = 0
                
            total_after = col.count_documents({})
            print(f"  - Collection [{col_name:<10}]: Ops={len(op_groups):<3} | Before={total_before:<5,} | Deleted={del_cnt:<5,} | Kept={total_after:<5,}")
        print()

    print("======================================================================")
    print(f"  PRUNING COMPLETE! Total Documents Removed: {total_pruned_across_all:,}")
    print("======================================================================")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prune MongoDB collections keeping latest N documents per operation.")
    parser.add_argument("--keep", type=int, default=5, help="Number of latest documents to keep per operation (default: 5)")
    args = parser.parse_args()
    prune_mongo(keep_count=args.keep)
