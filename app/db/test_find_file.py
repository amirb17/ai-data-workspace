from app.db.file_repository import find_physical_file_by_hash


file_hash = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"

result = find_physical_file_by_hash(file_hash)

print("Result:")
print(result)