from app.storage.s3_service import calculate_s3_object_hash


object_key = "staging/user-101/a75a1c0f-651b-4d37-9b10-a5a9f7f2790f/transactions.csv"

file_hash = calculate_s3_object_hash(object_key)

print("SHA-256:")
print(file_hash)