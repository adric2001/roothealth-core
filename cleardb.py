import boto3
import os

REGION = 'us-east-1'
TABLE_NAME = 'RootHealth_Stats' 

def clear_table():
    print(f"⚠️  WARNING: Deleting ALL data from {TABLE_NAME} in {REGION}...")
    confirm = input("Are you sure? (type 'yes' to confirm): ")
    
    if confirm.lower() != 'yes':
        print("Operation cancelled.")
        return

    dynamodb = boto3.resource('dynamodb', region_name=REGION)
    table = dynamodb.Table(TABLE_NAME)

    try:
        response = table.scan()
        items = response.get('Items', [])
        
        if not items:
            print("Table is already empty.")
            return

        print(f"Found {len(items)} items. Deleting...")

        with table.batch_writer() as batch:
            for item in items:
                batch.delete_item(
                    Key={
                        'user_id': item['user_id'],
                        'record_id': item['record_id']
                    }
                )
                
        print("✅ Success! Table cleared.")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    clear_table()