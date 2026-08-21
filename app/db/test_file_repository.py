from app.db.file_repository import create_physical_file


file = create_physical_file(
    file_name="transactions.csv",
    file_size=2048,
    file_hash="1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
)

print(file)