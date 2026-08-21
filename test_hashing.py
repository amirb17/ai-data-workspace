from app.utils.hashing import calculate_file_hash


file_hash = calculate_file_hash("datasets/test.csv")

print("SHA-256:")
print(file_hash)