from app.storage.s3_service import upload_file_to_s3


file_content = b"""transaction_id,customer_id,amount
1,101,500
2,102,750
3,103,1200
"""

s3_uri = upload_file_to_s3(
    file_content=file_content,
    file_name="test_transactions.csv",
    file_hash="test123"
)

print("Upload successful")
print("S3 URI:", s3_uri)