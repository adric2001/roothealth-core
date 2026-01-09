import boto3
import csv
import time
import os

s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

BUCKET_NAME = "roothealth-raw-files-adric" 
TABLE_NAME = "RootHealth_Stats"
USER_ID = "adric2001" 

def process_csv(filename):
    print(f"📂 Processing {filename}...")
    
    s3_filename = f"uploads/{USER_ID}/{int(time.time())}_bloodwork.csv"
    s3.upload_file(filename, BUCKET_NAME, s3_filename)
    print(f"✅ Raw file uploaded to S3: {s3_filename}")

    table = dynamodb.Table(TABLE_NAME)
    
    with open(filename, 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        
        with table.batch_writer() as batch:
            for row in reader:
                timestamp = int(time.time())
                metric_name = row['Metric']
                record_id = f"{metric_name}_{timestamp}"
                
                item = {
                    'user_id': USER_ID,
                    'record_id': record_id,
                    'metric': metric_name,
                    'value': row['Value'],      
                    'unit': row['Unit'],        
                    'range_low': row['Range_Low'],
                    'range_high': row['Range_High'],
                    'upload_timestamp': str(timestamp)
                }
                
                batch.put_item(Item=item)
                print(f"   -> Saved metric: {metric_name} = {row['Value']}")

    print("🚀 All data successfully ingested into RootHealth!")

if __name__ == "__main__":
    process_csv("sample_bloodwork.csv")